# LibrarianAI Desktop UI - Testing Checklist

Use this checklist to test the enhanced features after Claude Code implementation.

## TEST ENVIRONMENT

- Python 3.10+
- PyQt6 installed
- librarian.db in project root (1015 books)
- Books/ directory with PDF/EPUB files
- Feature branch: feature/desktop-ui

---

## FEATURE 1: DELETE BOOK (FROM DB AND DISK)

### Setup
- [ ] Have a test book in both DB and Books/ directory
- [ ] Note the filename, title, and filepath

### Test Cases

#### Test 1.1: Delete with confirmation
- [ ] Right-click a book
- [ ] Click "Delete"
- [ ] Confirmation dialog appears with book details
- [ ] Dialog shows checkbox: "Also delete file from disk"
- [ ] Click Cancel → Nothing happens, dialog closes
- [ ] Reopen and click Delete again

#### Test 1.2: Delete from DB only
- [ ] Right-click a book
- [ ] Click Delete
- [ ] **Uncheck** "Also delete file from disk"
- [ ] Click Delete button
- [ ] Book disappears from table
- [ ] Check database: `sqlite3 librarian.db "SELECT COUNT(*) FROM books;"`
  - Count decreased by 1 ✓
- [ ] Check Books/ directory: File still exists ✓

#### Test 1.3: Delete from DB and disk
- [ ] Right-click a book
- [ ] Click Delete
- [ ] **Check** "Also delete file from disk"
- [ ] Click Delete button
- [ ] Book disappears from table
- [ ] Check database: Count decreased by 1 ✓
- [ ] Check Books/ directory: File is gone ✓
- [ ] Status bar shows: "Deleted: [filename]" ✓

#### Test 1.4: Error handling - file already deleted
- [ ] Manually delete a file from Books/ directory (e.g., `rm Books/file.pdf`)
- [ ] Database still has the record
- [ ] Right-click the orphaned book
- [ ] Click Delete with "Also delete file from disk" checked
- [ ] No crash, warning logged
- [ ] Book removed from DB ✓

#### Test 1.5: Error handling - file locked
- [ ] Open a PDF in your default reader
- [ ] Try to delete the book with "Also delete file from disk" checked
- [ ] Error dialog appears: "Permission denied. File is in use or locked."
- [ ] Book NOT deleted from DB ✓
- [ ] Close the file, try again → Works ✓

#### Test 1.6: Cascading deletes
- [ ] Add a book to a shelf (if shelves are implemented)
- [ ] Delete the book
- [ ] Check: `sqlite3 librarian.db "SELECT * FROM book_shelves WHERE book_id = X;"`
- [ ] No orphaned records in book_shelves ✓

---

## FEATURE 2: COLUMN FILTERING (EXCEL-STYLE)

### Setup
- [ ] Main window with table fully loaded
- [ ] Table has columns: filename, title, author, year, category, status

### Test Cases

#### Test 2.1: Filter button appears on headers
- [ ] Look at column headers
- [ ] Each header should have a small dropdown arrow/button
- [ ] Hovering over header shows "Click to filter" tooltip ✓

#### Test 2.2: Open filter for one column
- [ ] Click dropdown arrow on "Status" column
- [ ] Filter dialog appears
- [ ] Shows all unique status values (e.g., "processing")
- [ ] Shows checkboxes for each value
- [ ] Shows "All" and "None" buttons ✓

#### Test 2.3: Filter with single value
- [ ] Click on "Category" column filter
- [ ] Uncheck "All"
- [ ] Check only "AI"
- [ ] Click Apply
- [ ] Table shows only books with category="AI" ✓
- [ ] Status bar shows: "Showing X books (1 filters active)" ✓

#### Test 2.4: Filter with multiple values
- [ ] Click "Category" filter again
- [ ] Check "AI", "Fiction", "Science"
- [ ] Click Apply
- [ ] Table shows books with category IN ("AI", "Fiction", "Science") ✓

#### Test 2.5: Multiple column filters (AND logic)
- [ ] Filter column 1 (Category): ["AI"]
- [ ] Filter column 2 (Status): ["extracted"]
- [ ] Table shows only books with BOTH conditions ✓
- [ ] Status bar shows: "Showing X books (2 filters active)" ✓

#### Test 2.6: Search in filter dropdown
- [ ] Click "Author" column filter
- [ ] Type in search box: "andrew"
- [ ] Only authors containing "andrew" show ✓
- [ ] Check/uncheck matches
- [ ] Apply

#### Test 2.7: Clear filters
- [ ] Apply several filters
- [ ] Click "Clear All" or manually uncheck all
- [ ] Table shows all books again
- [ ] Status bar shows: "Showing 1015 books (0 filters active)" ✓

#### Test 2.8: Filter header indicator
- [ ] Apply a filter to one column
- [ ] That column header should change appearance (bold, different color) ✓
- [ ] Hover shows which filters are active
- [ ] Clear filter → Header returns to normal ✓

#### Test 2.9: Filter persists during operations
- [ ] Apply filter (e.g., Status="extracted")
- [ ] Edit a book's metadata
- [ ] Delete a book
- [ ] Filter should still be active ✓
- [ ] Only showing filtered books ✓

#### Test 2.10: Performance with large result sets
- [ ] Apply a filter that shows many books (e.g., all books)
- [ ] Table updates smoothly (<500ms) ✓
- [ ] No UI freeze ✓
- [ ] Scrolling is smooth ✓

---

## FEATURE 3: FILE NAME MODIFICATION WITH PROTECTION

### Setup
- [ ] Have a test book with title, author, year populated in DB
- [ ] Example: Title="Machine Learning", Author="Andrew Ng", Year=2023

### Test Cases

#### Test 3.1: Rename dialog appears
- [ ] Right-click a book
- [ ] Click "Rename File"
- [ ] RenameFileDialog appears
- [ ] Shows current filename (read-only) ✓
- [ ] Shows suggested filename ✓

#### Test 3.2: Suggested filename generation
- [ ] For book with Title="Machine Learning", Author="Ng", Year=2023
- [ ] Suggested should be: "machine_learning_by_ng_2023.pdf" (or similar)
- [ ] Special characters removed ✓
- [ ] Extension preserved ✓
- [ ] Spaces converted to underscores ✓

#### Test 3.3: Suggested filename with missing fields
- [ ] Test book with only Title populated
- [ ] Suggested should be: "book_title.pdf" ✓
- [ ] Test book with Title and Author only (no Year)
- [ ] Suggested should be: "book_title_by_author.pdf" ✓

#### Test 3.4: Accept suggested filename
- [ ] Show rename dialog
- [ ] Click OK without editing
- [ ] File renamed on disk ✓
- [ ] DB updated with new filepath ✓
- [ ] Table refreshed with new filename ✓

#### Test 3.5: Manually edit filename
- [ ] Show rename dialog
- [ ] Clear suggested text
- [ ] Type: "my_custom_book_name"
- [ ] Should see: "✓ Valid filename" ✓
- [ ] Click Rename
- [ ] File renamed successfully ✓

#### Test 3.6: Validation - empty filename
- [ ] Show rename dialog
- [ ] Clear all text
- [ ] Should see: "✗ Filename cannot be empty" (red text) ✓
- [ ] Rename button should be disabled or error shown ✓

#### Test 3.7: Validation - invalid characters
- [ ] Show rename dialog
- [ ] Type: "book:name*invalid?.pdf"
- [ ] Should see: "✗ Contains invalid characters: \ / : \" * ? < > |" ✓
- [ ] Cannot save ✓

#### Test 3.8: Validation - path traversal attempt
- [ ] Show rename dialog
- [ ] Type: "../../../etc/passwd"
- [ ] Should see: "✗ Cannot contain paths (.., /, \\)" ✓

#### Test 3.9: Validation - missing extension
- [ ] Show rename dialog
- [ ] Type: "bookname"
- [ ] Should see: "✗ Must include file extension" ✓

#### Test 3.10: Extension preserved
- [ ] Book with .pdf extension
- [ ] Rename to: "my_book"
- [ ] System should preserve .pdf (show "my_book.pdf") ✓
- [ ] Rename to: "my_book.epub"
- [ ] If allowed, extension changes to .epub ✓

#### Test 3.11: File conflict detection
- [ ] Have two books: "file1.pdf" and "file2.pdf"
- [ ] Try to rename file2.pdf to "file1.pdf"
- [ ] Error dialog: "File already exists: file1.pdf" ✓
- [ ] Rename cancelled, DB unchanged ✓

#### Test 3.12: Error - file locked
- [ ] Open a PDF in reader
- [ ] Try to rename it
- [ ] Error: "Permission denied. File may be open in another app."
- [ ] Rename cancelled ✓
- [ ] Close PDF, rename succeeds ✓

#### Test 3.13: Rollback on error
- [ ] Manually create conflict (another app creates target file during dialog)
- [ ] Attempt rename
- [ ] Error shown
- [ ] DB unchanged ✓
- [ ] Original file still exists with original name ✓

#### Test 3.14: Special characters sanitization
- [ ] Title: "AI: The Future"
- [ ] Author: "John/Doe"
- [ ] Suggested: "ai_the_future_by_johndoe_[year].pdf" ✓
- [ ] Colons, slashes removed/replaced ✓

#### Test 3.15: Case normalization
- [ ] Title: "PYTHON Programming"
- [ ] Author: "Guido VAN Rossum"
- [ ] Suggested: "python_programming_by_guido_van_rossum.pdf" ✓
- [ ] Lowercase conversion applied ✓

#### Test 3.16: Unicode/international characters
- [ ] Title: "Машинное обучение" (Russian)
- [ ] Suggested should handle gracefully (not crash) ✓
- [ ] Either transliterate or replace with underscores ✓

---

## INTEGRATION TESTS

### Test I.1: Delete + Filter interaction
- [ ] Apply filter (e.g., Category="AI")
- [ ] Showing 5 books
- [ ] Delete one book
- [ ] Still showing 4 books
- [ ] Filter stays active ✓

### Test I.2: Rename + Filter interaction
- [ ] Apply filter
- [ ] Rename a book's file
- [ ] Filter still shows the renamed book (with new filename) ✓
- [ ] Clicking it opens correctly ✓

### Test I.3: All three features together
- [ ] Filter by category
- [ ] Rename one book in results
- [ ] Delete another
- [ ] UI stays consistent ✓

---

## PERFORMANCE & STRESS TESTS

### Test P.1: Delete many books
- [ ] Select 50 books
- [ ] Delete all (with confirmation)
- [ ] No UI freeze ✓
- [ ] All deleted from DB and disk ✓
- [ ] Takes <5 seconds ✓

### Test P.2: Complex filtering
- [ ] Apply 3-4 filters simultaneously
- [ ] Table updates smoothly (<100ms) ✓
- [ ] No lag during filtering ✓

### Test P.3: Rapid file renames
- [ ] Rename 10 books in quick succession
- [ ] All successful ✓
- [ ] DB consistent ✓
- [ ] No file corruptions ✓

---

## REGRESSION TESTS (Ensure no breaking changes)

### Test R.1: Core functionality still works
- [ ] Load books: <1 second ✓
- [ ] Search works ✓
- [ ] Edit metadata works ✓
- [ ] Open file in reader works ✓

### Test R.2: Database integrity
- [ ] After all tests, run: `sqlite3 librarian.db "PRAGMA integrity_check;"`
- [ ] Should return: "ok" ✓

### Test R.3: No orphaned files
- [ ] Check Books/ directory has no stranded files
- [ ] All remaining files have DB records ✓

---

## SUMMARY

- **Total Test Cases**: 70+
- **Expected Pass Rate**: 100%
- **Performance Targets**:
  - Table updates: <500ms
  - Delete: <1 second
  - Rename: <500ms
  - Filter apply: <100ms

---

## HOW TO USE THIS CHECKLIST

1. After Claude Code implements each feature, go through relevant tests
2. Check boxes as you verify
3. If any test fails, report to Claude Code with details
4. Retest after fixes
5. When all tests pass, feature is complete

---

**Last Updated**: 2026-04-08  
**Status**: Ready to test
