# LibrarianAI Desktop UI - Enhanced Features Prompt

This prompt covers three interconnected features for the main_window.py and related dialogs:
1. Delete book (from DB and disk)
2. Column filtering (Excel-style headers)
3. File name modification with protection

## CONTEXT

Working on the feature/desktop-ui branch of Librarian.Desktop.
Building PyQt6 desktop UI that reuses backend logic.
Database: librarian.db (1015 books)
Files stored in: Books/ directory (relative paths)

Read MASTER_PROMPT_PYQT_DESKTOP.md for general context.

---

## FEATURE 1: DELETE BOOK (FROM DB AND DISK)

### Requirement
When user clicks "Delete" on a book:
1. Show confirmation dialog with details (filename, title, author)
2. Ask: "Delete from database only" OR "Delete from database AND disk"
3. If "Delete from disk":
   - Remove file from Books/ directory
   - Handle errors gracefully (file already deleted, permissions, etc.)
4. Remove database record
5. Refresh UI table
6. Show success/error message

### Implementation Details

**Dialog Flow**:
```
User right-clicks book row or clicks Delete button
    ↓
BookDeleteConfirmDialog appears:
  "Are you sure you want to delete [filename]?"
  "Title: [title]"
  "Author: [author]"
  
  [ ] Also delete file from disk
  
  [Delete] [Cancel]
```

**Code Pattern**:
```python
def on_delete_book(self, book_id):
    book = self.db.get_book(book_id)
    dialog = BookDeleteConfirmDialog(book)
    if dialog.exec():
        delete_file_too = dialog.should_delete_file()
        try:
            # Delete file if requested
            if delete_file_too:
                file_path = os.path.join(os.getcwd(), book['filepath'])
                if os.path.exists(file_path):
                    os.remove(file_path)
                    log.info(f"Deleted file: {file_path}")
                else:
                    log.warning(f"File not found: {file_path}")
            
            # Delete database record
            self.db.execute("DELETE FROM books WHERE id = ?", (book_id,))
            self.db.execute("DELETE FROM book_shelves WHERE book_id = ?", (book_id,))
            
            # Refresh UI
            self.refresh_table()
            self.status_bar.showMessage(f"Deleted: {book['filename']}")
        
        except PermissionError:
            self.show_error("Permission denied. File is in use or locked.")
        except Exception as e:
            self.show_error(f"Error deleting book: {str(e)}")
```

**Error Handling**:
- File doesn't exist (deleted manually) → Log warning, delete DB record anyway
- File is locked (PDF open in reader) → Show error, don't delete DB record
- Permission denied → Show error message to user
- Database error → Rollback, show error

**New Dialog: BookDeleteConfirmDialog**
```python
class BookDeleteConfirmDialog(QDialog):
    def __init__(self, book, parent=None):
        super().__init__(parent)
        self.book = book
        self.setWindowTitle("Confirm Delete")
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Book info
        layout.addWidget(QLabel(f"Filename: {self.book['filename']}"))
        layout.addWidget(QLabel(f"Title: {self.book['title'] or 'N/A'}"))
        layout.addWidget(QLabel(f"Author: {self.book['author'] or 'N/A'}"))
        layout.addSpacing(10)
        
        # Delete file checkbox
        self.delete_file_checkbox = QCheckBox("Also delete file from disk")
        layout.addWidget(self.delete_file_checkbox)
        
        # Buttons
        buttons = QHBoxLayout()
        delete_btn = QPushButton("Delete")
        cancel_btn = QPushButton("Cancel")
        delete_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(delete_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)
        
        self.setLayout(layout)
    
    def should_delete_file(self):
        return self.delete_file_checkbox.isChecked()
```

---

## FEATURE 2: COLUMN FILTERING (EXCEL-STYLE HEADERS)

### Requirement
Each column header should allow:
1. Click dropdown arrow in header → Show filter menu
2. Filter options:
   - Checkbox list of unique values (with "All", "None" quick buttons)
   - Text search (for long lists like filenames)
3. Apply filter → Table updates immediately
4. Show indicator when column is filtered (bold or different color header)
5. Clear filter → Back to showing all rows

### Implementation Details

**Header Filter UI**:
```
[Filename ▼]  [Title ▼]  [Author ▼]  [Year ▼]  [Category ▼]  [Status ▼]

Click "Category ▼":
┌─────────────────────────┐
│ 🔍 Filter...            │
├─────────────────────────┤
│ ☑ All      [Clear All]  │
│ ☑ AI                    │
│ ☑ Fiction               │
│ ☑ Programming           │
│ ☑ Science               │
├─────────────────────────┤
│     [Apply] [Cancel]    │
└─────────────────────────┘
```

**Code Pattern**:
```python
# Create filterable table
self.table = FilterableTableWidget()

# Add columns with filter support
self.table.add_column("filename", "Filename", filterable=True)
self.table.add_column("title", "Title", filterable=True)
self.table.add_column("author", "Author", filterable=True)
self.table.add_column("year", "Year", filterable=True)
self.table.add_column("category", "Category", filterable=True)
self.table.add_column("status", "Status", filterable=True)

# Connect filter changed signal
self.table.filterChanged.connect(self.on_filter_changed)

def on_filter_changed(self, filters):
    # filters = {"category": ["AI", "Fiction"], "status": ["extracted"]}
    self.apply_filters(filters)

def apply_filters(self, filters):
    # Rebuild query with WHERE clauses
    query = "SELECT * FROM books WHERE 1=1"
    params = []
    
    if "category" in filters and filters["category"]:
        placeholders = ",".join("?" * len(filters["category"]))
        query += f" AND category IN ({placeholders})"
        params.extend(filters["category"])
    
    if "status" in filters and filters["status"]:
        placeholders = ",".join("?" * len(filters["status"]))
        query += f" AND status IN ({placeholders})"
        params.extend(filters["status"])
    
    # ... other filters
    
    books = self.db.query(query, params)
    self.display_table(books)
    self.status_bar.showMessage(f"Showing {len(books)} books ({len(filters)} filters active)")
```

**New Class: FilterableTableWidget**

This wraps QTableWidget and adds header filtering:

```python
class FilterableTableWidget(QTableWidget):
    filterChanged = pyqtSignal(dict)  # Emits {column: [filter_values]}
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filters = {}
        self.columns_config = {}
        
        # Replace header with custom header
        self.setHorizontalHeader(FilterHeaderView(self.horizontalHeader()))
        self.horizontalHeader().filterClicked.connect(self.on_filter_clicked)
    
    def add_column(self, col_id, col_name, filterable=True):
        self.columns_config[col_id] = {
            "name": col_name,
            "filterable": filterable
        }
    
    def on_filter_clicked(self, column_index):
        if not self.columns_config[column_index]["filterable"]:
            return
        
        # Get unique values for this column
        unique_values = self.get_unique_column_values(column_index)
        
        # Show filter dialog
        dialog = ColumnFilterDialog(unique_values, self.filters.get(column_index, []))
        if dialog.exec():
            selected = dialog.get_selected_values()
            if selected:
                self.filters[column_index] = selected
            else:
                self.filters.pop(column_index, None)
            
            self.filterChanged.emit(self.filters)

class FilterHeaderView(QHeaderView):
    filterClicked = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.sectionClicked.connect(self.on_section_clicked)
    
    def on_section_clicked(self, column_index):
        self.filterClicked.emit(column_index)

class ColumnFilterDialog(QDialog):
    def __init__(self, unique_values, current_selection, parent=None):
        super().__init__(parent)
        self.unique_values = sorted(set(unique_values))
        self.current_selection = current_selection or self.unique_values
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Search box
        search = QLineEdit()
        search.setPlaceholderText("🔍 Filter...")
        layout.addWidget(search)
        
        # All/None buttons
        buttons = QHBoxLayout()
        all_btn = QPushButton("All")
        none_btn = QPushButton("None")
        all_btn.clicked.connect(self.select_all)
        none_btn.clicked.connect(self.select_none)
        buttons.addWidget(all_btn)
        buttons.addWidget(none_btn)
        layout.addLayout(buttons)
        
        # Checkbox list
        self.list_widget = QListWidget()
        for value in self.unique_values:
            item = QListWidgetItem(str(value))
            item.setCheckState(Qt.Checked if value in self.current_selection else Qt.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)
        
        # Search filter
        search.textChanged.connect(self.filter_list)
        
        # OK/Cancel
        dialog_buttons = QHBoxLayout()
        ok_btn = QPushButton("Apply")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        dialog_buttons.addWidget(ok_btn)
        dialog_buttons.addWidget(cancel_btn)
        layout.addLayout(dialog_buttons)
        
        self.setLayout(layout)
        self.setWindowTitle("Filter Column")
        self.resize(300, 400)
    
    def filter_list(self, text):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text.lower() not in item.text().lower())
    
    def select_all(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Checked)
    
    def select_none(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Unchecked)
    
    def get_selected_values(self):
        selected = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                selected.append(item.text())
        return selected
```

---

## FEATURE 3: FILE NAME MODIFICATION WITH PROTECTION

### Requirement
When user right-clicks a book and selects "Rename File":
1. Show dialog with suggested file name
2. Suggested name = f"{title}_{author}_{year}.{extension}"
   - Sanitize for filesystem (remove special chars)
   - If any field is missing, use available ones
   - Example: "machine_learning_by_andrew_ng_2023.pdf"
3. User can:
   - Accept suggested name (just click OK)
   - Manually edit name
4. Protection mechanism:
   - Don't allow:
     * Empty filename
     * Invalid characters (\ / : * ? " < > |)
     * Paths with .. or /
   - Prevent accidental deletion (preserve extension)
5. On save:
   - Rename file on disk
   - Update filepath in DB
   - Handle conflicts (file already exists)
   - Rollback if file rename fails

### Implementation Details

**Dialog Flow**:
```
User right-clicks book → "Rename File"
    ↓
RenameFileDialog appears:
  Current: document_2023.pdf
  Suggested: machine_learning_by_andrew_ng_2023.pdf
  
  [filename_textbox]
  
  ℹ️ Tip: File extension will be preserved
  ℹ️ Tip: Invalid chars will be removed: \ / : * ? " < > |
  
  [Rename] [Cancel]
```

**Code Pattern**:
```python
def on_rename_file(self, book_id):
    book = self.db.get_book(book_id)
    
    # Generate suggested name
    suggested = self.generate_suggested_filename(book)
    
    dialog = RenameFileDialog(book['filename'], suggested)
    if dialog.exec():
        new_filename = dialog.get_new_filename()
        try:
            # Rename file on disk
            old_path = os.path.join(os.getcwd(), book['filepath'])
            new_path = os.path.join(os.path.dirname(old_path), new_filename)
            
            # Check for conflicts
            if os.path.exists(new_path) and new_path != old_path:
                self.show_error(f"File already exists: {new_filename}")
                return
            
            # Rename
            os.rename(old_path, new_path)
            
            # Update DB with new filepath
            new_filepath = os.path.relpath(new_path, os.getcwd())
            self.db.execute(
                "UPDATE books SET filename = ?, filepath = ? WHERE id = ?",
                (new_filename, new_filepath, book_id)
            )
            
            self.refresh_table()
            self.status_bar.showMessage(f"Renamed to: {new_filename}")
        
        except FileExistsError:
            self.show_error(f"File already exists: {new_filename}")
        except PermissionError:
            self.show_error("Permission denied. File may be open in another app.")
        except Exception as e:
            self.show_error(f"Error renaming file: {str(e)}")

def generate_suggested_filename(self, book):
    """Generate filename from title, author, year"""
    parts = []
    if book.get('title'):
        parts.append(self.sanitize_filename(book['title']))
    if book.get('author'):
        parts.append(f"by_{self.sanitize_filename(book['author'])}")
    if book.get('year'):
        parts.append(str(book['year']))
    
    base_name = "_".join(parts) if parts else "unnamed"
    
    # Get extension from original file
    _, ext = os.path.splitext(book['filename'])
    
    return f"{base_name}{ext}"

def sanitize_filename(self, filename):
    """Remove invalid filesystem characters from filename"""
    import re
    # Remove invalid chars
    filename = re.sub(r'[\\/:"*?<>|]', '', filename)
    # Remove leading/trailing spaces
    filename = filename.strip()
    # Replace spaces with underscores
    filename = filename.replace(" ", "_")
    # Remove multiple underscores
    filename = re.sub(r'_+', '_', filename)
    return filename.lower()
```

**New Dialog: RenameFileDialog**

```python
class RenameFileDialog(QDialog):
    def __init__(self, current_filename, suggested_filename, parent=None):
        super().__init__(parent)
        self.current_filename = current_filename
        self.suggested_filename = suggested_filename
        self.valid_filename = None
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Current filename (read-only)
        layout.addWidget(QLabel("Current:"))
        current_display = QLineEdit()
        current_display.setText(self.current_filename)
        current_display.setReadOnly(True)
        current_display.setStyleSheet("background-color: #f0f0f0;")
        layout.addWidget(current_display)
        
        layout.addSpacing(10)
        
        # Suggested filename (editable)
        layout.addWidget(QLabel("New:"))
        self.filename_input = QLineEdit()
        self.filename_input.setText(self.suggested_filename)
        self.filename_input.textChanged.connect(self.on_filename_changed)
        layout.addWidget(self.filename_input)
        
        # Validation message
        self.validation_label = QLabel()
        self.validation_label.setStyleSheet("color: green;")
        layout.addWidget(self.validation_label)
        
        # Tips
        tips = QLabel(
            "💡 Tips:\n"
            "• File extension will be preserved\n"
            "• Invalid characters (\\ / : * ? \" < > |) will be removed\n"
            "• Leading/trailing spaces will be trimmed"
        )
        tips.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(tips)
        
        # Buttons
        buttons = QHBoxLayout()
        rename_btn = QPushButton("Rename")
        cancel_btn = QPushButton("Cancel")
        rename_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(rename_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)
        
        self.setLayout(layout)
        self.setWindowTitle("Rename File")
        self.resize(400, 250)
        
        # Initial validation
        self.on_filename_changed()
    
    def on_filename_changed(self):
        filename = self.filename_input.text()
        is_valid, message = self.validate_filename(filename)
        
        if is_valid:
            self.validation_label.setText("✓ Valid filename")
            self.validation_label.setStyleSheet("color: green;")
            self.valid_filename = filename
        else:
            self.validation_label.setText(f"✗ {message}")
            self.validation_label.setStyleSheet("color: red;")
            self.valid_filename = None
    
    def validate_filename(self, filename):
        """Check if filename is valid"""
        import re
        
        if not filename or not filename.strip():
            return False, "Filename cannot be empty"
        
        # Check for invalid characters
        invalid_chars = r'[\\/:"*?<>|]'
        if re.search(invalid_chars, filename):
            return False, "Contains invalid characters: \\ / : \" * ? < > |"
        
        # Check for path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            return False, "Cannot contain paths (.., /, \\)"
        
        # Check extension preserved
        if "." not in filename:
            return False, "Must include file extension"
        
        return True, "Valid filename"
    
    def get_new_filename(self):
        return self.valid_filename if self.valid_filename else self.filename_input.text().strip()
```

---

## INTEGRATION POINTS

### In main_window.py
```python
def on_book_right_clicked(self, position):
    menu = QMenu(self)
    menu.addAction("Edit Metadata", self.on_edit_book)
    menu.addAction("Rename File", self.on_rename_file)
    menu.addAction("Open in Explorer", self.on_open_in_explorer)
    menu.addSeparator()
    menu.addAction("Delete", self.on_delete_book)
    menu.exec(self.table.mapToGlobal(position))

def on_delete_book(self):
    book_id = self.get_selected_book_id()
    if book_id:
        self.delete_book(book_id)

def on_rename_file(self):
    book_id = self.get_selected_book_id()
    if book_id:
        self.rename_file(book_id)
```

### Column filtering in main_window.py
```python
def setup_table(self):
    self.table = FilterableTableWidget()
    self.table.add_column("filename", "Filename", filterable=True)
    self.table.add_column("title", "Title", filterable=True)
    self.table.add_column("author", "Author", filterable=True)
    self.table.add_column("year", "Year", filterable=True)
    self.table.add_column("category", "Category", filterable=True)
    self.table.add_column("status", "Status", filterable=True)
    self.table.filterChanged.connect(self.apply_filters)
```

---

## SUCCESS CRITERIA

When complete:
- ✅ Delete book removes from both DB and disk (with confirmation)
- ✅ Each column header has filter dropdown (Excel-style)
- ✅ Filters work correctly (AND logic for multiple filters)
- ✅ File rename dialog shows suggested name
- ✅ File rename validates input (no invalid chars, no path traversal)
- ✅ File renamed on disk AND in DB
- ✅ Error handling for all edge cases
- ✅ User-friendly messages for errors
- ✅ Table refreshes after any operation

---

## VERSION
v1 - 2026-04-08 - Enhanced features: delete, filtering, file rename
