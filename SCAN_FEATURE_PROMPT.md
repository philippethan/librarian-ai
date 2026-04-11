# LibrarianAI Desktop UI - Scan Feature Prompt

Add a "Scan" button to the PyQt6 desktop application that discovers and extracts metadata from new books in the Books/ directory.

## CONTEXT

Working on: feature/desktop-ui branch of Librarian.Desktop
Existing: app.py contains scanning and extraction logic
Goal: Adapt app.py logic into PyQt UI as a button + progress dialog

Current database state: 1015 books, mostly with status='processing'
Books directory: Books/ (contains PDF/EPUB files)

Read MASTER_PROMPT_PYQT_DESKTOP.md for general context.

---

## FEATURE OVERVIEW

### What "Scan" Does

The Scan feature:
1. **Discovers new books** in Books/ directory
   - Finds all PDF/EPUB files
   - Checks if already in database (by filename or file_hash)
   - Identifies files not yet in DB
2. **Extracts metadata** from new books
   - Uses existing backend.extractor functions
   - Extracts: title, author, year, language, description, etc.
   - Computes file_hash for deduplication
3. **Detects duplicates** of newly discovered books
   - Against existing library
   - Against other newly discovered books
4. **Adds to database**
   - Creates book records with extracted metadata
   - Sets status = 'extracted' (if extraction successful)
   - Sets status = 'error' (if extraction failed)
5. **Reports results**
   - Shows progress during scan
   - Reports: X new books found, Y extracted, Z errors
   - Shows detailed errors for investigation

### User Flow

```
User clicks "Scan" button in main_window
    ↓
ScanProgressDialog appears (modeless)
    ├─ "Scanning Books/ directory..."
    ├─ Progress bar: [=====>    ] 45/1200 files
    ├─ "Extracting: document_2023.pdf..."
    └─ Cancel button (stops scan gracefully)
    ↓
Scan completes:
    "Scan Complete!
     New books found: 47
     Successfully extracted: 45
     Extraction errors: 2
     Duplicates detected: 3
     [View Results] [Close]"
    ↓
If errors, show:
    "Errors (2):
     - file1.pdf: PDF corrupted
     - file2.pdf: Unsupported format"
```

---

## REUSING app.py LOGIC

Your existing app.py likely has:

```python
# pseudo-code from your app.py
def scan_directory(books_dir):
    """Find all PDF/EPUB files"""
    return list of files

def extract_metadata(filepath):
    """Extract title, author, year, etc."""
    return metadata dict

def compute_file_hash(filepath):
    """SHA256 hash for deduplication"""
    return hash

def detect_duplicates(metadata, existing_books):
    """Check if already in library"""
    return list of potential duplicates

def process_new_books(new_files, extractor, database):
    """Main processing loop"""
    for file in new_files:
        try:
            metadata = extract_metadata(file)
            hash = compute_file_hash(file)
            duplicates = detect_duplicates(metadata, database)
            if not duplicates:
                database.insert_book(metadata, hash)
        except Exception as e:
            log_error(file, e)
```

**For the PyQt UI:**
- **Do this**: Import and call these functions from your existing modules
- **Don't do this**: Rewrite them in the UI code

Example:
```python
# In ui/scan_service.py
from backend.extractor import extract_metadata_from_file
from backend.duplicates import check_duplicates
from backend.database import get_connection

class ScanService:
    def scan_and_extract(self, books_dir, progress_callback):
        """Scan directory and extract metadata"""
        # Progress callback for UI updates
        # progress_callback(current, total, message)
        
        files = self.find_unindexed_files(books_dir)
        total = len(files)
        
        for idx, filepath in enumerate(files):
            try:
                # Use backend functions
                metadata = extract_metadata_from_file(filepath)
                file_hash = self.compute_hash(filepath)
                duplicates = check_duplicates(metadata, self.db)
                
                # Report progress
                progress_callback(idx+1, total, f"Extracting: {os.path.basename(filepath)}")
                
                if not duplicates:
                    self.db.insert_book(metadata, file_hash, filepath)
                    self.results['extracted'] += 1
                else:
                    self.results['duplicates'] += 1
            
            except Exception as e:
                self.results['errors'].append((filepath, str(e)))
        
        return self.results
```

---

## IMPLEMENTATION STRUCTURE

### New Files to Create

```
ui/
├── scan/                          # NEW: Scan functionality
│   ├── __init__.py
│   ├── scan_service.py            # Backend logic (discovery, extraction)
│   └── scan_dialog.py             # UI for progress and results
```

### In main_window.py

Add to toolbar/menu:
```python
# Add to MainWindow.__init__
scan_btn = QPushButton("Scan")
scan_btn.clicked.connect(self.on_scan_clicked)
toolbar.addWidget(scan_btn)

# Add method
def on_scan_clicked(self):
    dialog = ScanProgressDialog(self.db)
    dialog.exec()
    # After scan completes, refresh table
    self.refresh_table()
```

---

## FEATURE 1: DISCOVERY (Find Unindexed Files)

### Requirement
Scan Books/ directory and find files not yet in database.

### Implementation

```python
class ScanService:
    def __init__(self, db, books_dir='Books'):
        self.db = db
        self.books_dir = books_dir
        self.results = {
            'total_files': 0,
            'new_files': 0,
            'already_indexed': 0,
            'extracted': 0,
            'duplicates': 0,
            'errors': []
        }
    
    def find_unindexed_files(self):
        """Find PDF/EPUB files not in database"""
        all_files = self._get_all_files()  # All PDFs/EPUBs in Books/
        indexed_files = self._get_indexed_files()  # In DB
        
        unindexed = [f for f in all_files if f not in indexed_files]
        
        self.results['total_files'] = len(all_files)
        self.results['new_files'] = len(unindexed)
        self.results['already_indexed'] = len(indexed_files)
        
        return unindexed
    
    def _get_all_files(self):
        """Get all PDF/EPUB files in Books/"""
        all_files = []
        books_path = os.path.join(os.getcwd(), self.books_dir)
        
        for root, dirs, files in os.walk(books_path):
            for file in files:
                if file.lower().endswith(('.pdf', '.epub')):
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, os.getcwd())
                    all_files.append(relative_path)
        
        return all_files
    
    def _get_indexed_files(self):
        """Get all files already in database"""
        rows = self.db.query("SELECT filepath FROM books")
        return [row['filepath'] for row in rows]
```

---

## FEATURE 2: EXTRACTION (Get Metadata)

### Requirement
Extract metadata from each new book using existing backend functions.

### Implementation

```python
from backend.extractor import extract_metadata_from_file
from backend.duplicates import check_duplicates
import hashlib

class ScanService:
    def extract_metadata_batch(self, file_paths, progress_callback=None):
        """Extract metadata from list of files"""
        results = []
        
        for idx, filepath in enumerate(file_paths):
            try:
                # Full path
                full_path = os.path.join(os.getcwd(), filepath)
                
                # Extract using backend
                metadata = extract_metadata_from_file(full_path)
                
                # Compute hash for deduplication
                file_hash = self._compute_file_hash(full_path)
                
                # Get file size
                file_size = os.path.getsize(full_path)
                
                # Get filename
                filename = os.path.basename(filepath)
                
                result = {
                    'filename': filename,
                    'filepath': filepath,
                    'metadata': metadata,
                    'file_hash': file_hash,
                    'file_size': file_size,
                    'status': 'extracted',
                    'error': None
                }
                
                results.append(result)
                
                # Report progress
                if progress_callback:
                    progress_callback(
                        current=idx + 1,
                        total=len(file_paths),
                        message=f"Extracting: {filename}"
                    )
            
            except Exception as e:
                # Log error but continue
                result = {
                    'filename': os.path.basename(filepath),
                    'filepath': filepath,
                    'metadata': {},
                    'file_hash': None,
                    'file_size': None,
                    'status': 'error',
                    'error': str(e)
                }
                results.append(result)
                self.results['errors'].append((filepath, str(e)))
                
                if progress_callback:
                    progress_callback(
                        current=idx + 1,
                        total=len(file_paths),
                        message=f"Error: {filename}"
                    )
        
        return results
    
    def _compute_file_hash(self, filepath):
        """Compute SHA256 hash of file"""
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
```

---

## FEATURE 3: DEDUPLICATION (Check for Existing Books)

### Requirement
Detect if new books are duplicates of existing library or each other.

### Implementation

```python
class ScanService:
    def check_duplicates_batch(self, extracted_books, progress_callback=None):
        """Check each extracted book for duplicates"""
        # Get existing library for comparison
        existing_books = self.db.query("SELECT * FROM books")
        
        for idx, book in enumerate(extracted_books):
            if book['status'] != 'extracted':
                continue  # Skip books with extraction errors
            
            try:
                # Use backend duplicate detection
                duplicates = check_duplicates(
                    book['metadata'],
                    book['file_hash'],
                    existing_books
                )
                
                if duplicates:
                    book['is_duplicate'] = True
                    book['duplicate_of'] = duplicates[0]['id']  # Link to original
                    self.results['duplicates'] += 1
                else:
                    book['is_duplicate'] = False
                    book['duplicate_of'] = None
                
                if progress_callback:
                    progress_callback(
                        current=idx + 1,
                        total=len(extracted_books),
                        message=f"Checking duplicates: {book['filename']}"
                    )
            
            except Exception as e:
                book['error'] = f"Duplicate check failed: {str(e)}"
                self.results['errors'].append((book['filename'], str(e)))
        
        return extracted_books
```

---

## FEATURE 4: DATABASE INSERTION (Add New Books)

### Requirement
Insert successfully extracted books into database.

### Implementation

```python
class ScanService:
    def insert_new_books(self, books_to_insert, progress_callback=None):
        """Insert new books into database"""
        inserted = 0
        
        for idx, book in enumerate(books_to_insert):
            if book['status'] != 'extracted' or book['is_duplicate']:
                continue  # Skip errors and duplicates
            
            try:
                # Build insert record
                record = {
                    'filename': book['filename'],
                    'filepath': book['filepath'],
                    'status': 'extracted',
                    'file_hash': book['file_hash'],
                    'file_size': book['file_size'],
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat(),
                    # Extracted metadata
                    'title': book['metadata'].get('title'),
                    'author': book['metadata'].get('author'),
                    'year': book['metadata'].get('year'),
                    'language': book['metadata'].get('language'),
                    'description': book['metadata'].get('description'),
                    'tags': book['metadata'].get('tags'),
                }
                
                # Insert
                self.db.execute(
                    """INSERT INTO books 
                       (filename, filepath, status, file_hash, file_size, 
                        created_at, updated_at, title, author, year, 
                        language, description, tags)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record['filename'], record['filepath'], record['status'],
                     record['file_hash'], record['file_size'],
                     record['created_at'], record['updated_at'],
                     record['title'], record['author'], record['year'],
                     record['language'], record['description'], record['tags'])
                )
                
                inserted += 1
                self.results['extracted'] += 1
                
                if progress_callback:
                    progress_callback(
                        current=idx + 1,
                        total=len(books_to_insert),
                        message=f"Inserting: {book['filename']}"
                    )
            
            except Exception as e:
                self.results['errors'].append((book['filename'], str(e)))
    
    def scan_and_extract_all(self, progress_callback=None):
        """Main scan process: discovery → extraction → dedup → insert"""
        try:
            # Step 1: Discover
            unindexed = self.find_unindexed_files()
            if not unindexed:
                if progress_callback:
                    progress_callback(0, 0, "No new books found")
                return self.results
            
            # Step 2: Extract
            extracted = self.extract_metadata_batch(unindexed, progress_callback)
            
            # Step 3: Check duplicates
            checked = self.check_duplicates_batch(extracted, progress_callback)
            
            # Step 4: Insert
            self.insert_new_books(checked, progress_callback)
            
            return self.results
        
        except Exception as e:
            self.results['errors'].append(('scan_process', str(e)))
            return self.results
```

---

## UI: SCAN PROGRESS DIALOG

### ScanProgressDialog

```python
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton
from PyQt6.QtCore import QThread, pyqtSignal
from concurrent.futures import ThreadPoolExecutor

class ScanProgressDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.scan_service = None
        self.scan_thread = None
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Status message
        self.status_label = QLabel("Preparing to scan...")
        layout.addWidget(self.status_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        layout.addWidget(self.progress_bar)
        
        # Details
        self.details_label = QLabel("")
        layout.addWidget(self.details_label)
        
        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_scan)
        layout.addWidget(cancel_btn)
        
        self.setLayout(layout)
        self.setWindowTitle("Scan Books Directory")
        self.setMinimumWidth(500)
        self.setModal(False)  # Non-blocking
    
    def exec(self):
        # Start scan when dialog opens
        self.start_scan()
        return super().exec()
    
    def start_scan(self):
        """Start scan in background thread"""
        self.scan_service = ScanService(self.db)
        
        # Run in thread to not block UI
        self.scan_thread = Thread(target=self._scan_worker)
        self.scan_thread.daemon = True
        self.scan_thread.start()
    
    def _scan_worker(self):
        """Background worker for scanning"""
        try:
            results = self.scan_service.scan_and_extract_all(
                progress_callback=self.update_progress
            )
            
            # Show results
            self.show_results(results)
        
        except Exception as e:
            self.show_error(f"Scan failed: {str(e)}")
    
    def update_progress(self, current, total, message):
        """Called from scan_service to update UI"""
        # Must be thread-safe
        if total > 0:
            percentage = (current / total) * 100
            self.progress_bar.setValue(int(percentage))
        
        self.status_label.setText(message)
        self.details_label.setText(f"{current}/{total} files processed")
    
    def show_results(self, results):
        """Show scan results"""
        summary = f"""
        Scan Complete!
        
        Total files found: {results['total_files']}
        Already indexed: {results['already_indexed']}
        New books: {results['new_files']}
        
        Successfully extracted: {results['extracted']}
        Duplicates detected: {results['duplicates']}
        Errors: {len(results['errors'])}
        """
        
        self.status_label.setText(summary)
        
        if results['errors']:
            errors_text = "\n".join([f"- {f}: {e}" for f, e in results['errors']])
            self.details_label.setText(f"Errors:\n{errors_text}")
    
    def show_error(self, error_msg):
        """Show error message"""
        self.status_label.setText(f"Error: {error_msg}")
    
    def cancel_scan(self):
        """Cancel scan (graceful shutdown)"""
        if self.scan_thread and self.scan_thread.is_alive():
            # Signal scan to stop (implement in ScanService if needed)
            pass
        self.reject()
```

---

## INTEGRATION WITH main_window.py

### Add Scan Button

```python
# In MainWindow.__init__

# Create toolbar
toolbar = self.addToolBar("Tools")

# Add scan button
scan_btn = QPushButton("📁 Scan Books Directory")
scan_btn.setToolTip("Scan Books/ for new files and extract metadata")
scan_btn.clicked.connect(self.on_scan_clicked)
toolbar.addWidget(scan_btn)

# Add separator
toolbar.addSeparator()

# Other buttons...
refresh_btn = QPushButton("🔄 Refresh")
refresh_btn.clicked.connect(self.refresh_table)
toolbar.addWidget(refresh_btn)
```

### Handle Scan Completion

```python
def on_scan_clicked(self):
    """Launch scan dialog"""
    dialog = ScanProgressDialog(self.db, self)
    if dialog.exec():
        # After scan, refresh table to show new books
        self.refresh_table()
        self.status_bar.showMessage("Scan complete! New books added to library.")
```

---

## THREADING & PERFORMANCE

### Requirements
- Scan must not block UI
- Use background thread (Thread or ThreadPoolExecutor)
- Progress updates must be thread-safe
- Can cancel scan mid-operation
- Handle 1000+ files efficiently

### Implementation Pattern

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

# Option 1: Simple Thread (recommended)
def start_scan(self):
    self.scan_thread = Thread(target=self._scan_worker)
    self.scan_thread.daemon = True
    self.scan_thread.start()

# Option 2: ThreadPoolExecutor (for parallel extraction)
def start_scan(self):
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Can extract 4 files in parallel
        futures = [
            executor.submit(extract_metadata_from_file, file)
            for file in unindexed_files
        ]
        # Wait for all to complete
```

---

## ERROR HANDLING

### Expected Errors

1. **Corrupted PDF/EPUB** → Skip, log error, continue
2. **Permission denied** → Skip file, log warning
3. **Unsupported format** → Skip, log error
4. **Duplicate detected** → Don't insert, mark as duplicate
5. **Database insert fails** → Roll back, show error

### Implementation

```python
try:
    metadata = extract_metadata_from_file(filepath)
except CorruptedPDFError as e:
    results['errors'].append((filepath, "PDF corrupted"))
except PermissionError as e:
    results['errors'].append((filepath, "Permission denied"))
except Exception as e:
    results['errors'].append((filepath, str(e)))
    continue  # Skip this file, process others
```

---

## SUCCESS CRITERIA

When complete, you should be able to:
- ✅ Click "Scan" button in main window
- ✅ Scan discovers all PDF/EPUB files in Books/
- ✅ Identifies files not in database
- ✅ Extracts metadata from new files (reusing backend)
- ✅ Detects duplicates
- ✅ Inserts new books to database
- ✅ Shows progress during scanning
- ✅ Reports results (X new books, Y errors)
- ✅ Handles errors gracefully
- ✅ UI remains responsive (no freeze)
- ✅ Can cancel scan mid-operation
- ✅ New books appear in table after scan

---

## VERSION
v1 - 2026-04-08 - Scan feature for discovering and extracting new books
