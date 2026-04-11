"""
ui/book_detail_dialog.py — Modal dialog for viewing and editing book metadata.

Usage from MainWindow:
    dialog = BookDetailDialog(book_id, db_path, parent=self)
    result = dialog.exec()
    if result == QDialog.DialogCode.Accepted:   # Save pressed
        self._refresh_book_row(book_id)
    elif result == BookDetailDialog.DELETED:    # Delete pressed
        self._remove_book_row(book_id)
"""

import json
import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.db import get_conn

log = logging.getLogger(__name__)

DB_PATH: str = os.environ.get("DB_PATH", "backend/data/librarian.db")

# Pull taxonomy from the single source of truth in the backend.
# Falls back to an empty dict if the import fails (e.g. missing deps at startup).
try:
    from backend.app import FIXED_TAXONOMY as _TAXONOMY  # noqa: PLC0415
except Exception:
    log.warning("Could not import FIXED_TAXONOMY from backend.app — category dropdowns will be empty")
    _TAXONOMY: dict[str, list[str]] = {}

_DIFFICULTY_OPTIONS: list[str] = ["", "Beginner", "Intermediate", "Advanced", "Expert"]
_READING_STATUS_OPTIONS: list[str] = ["to-read", "reading", "read", "abandoned"]

# Fields we write to SQLite; mirrors backend PATCHABLE_FIELDS + reading_status.
_WRITABLE: frozenset[str] = frozenset({
    "title", "author", "year", "language",
    "category", "subcategory", "difficulty",
    "description", "tags", "reading_status",
})


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class BookDetailDialog(QDialog):
    """Edit a book's metadata.  Returns Accepted on save, DELETED on delete."""

    DELETED: int = 2   # custom result code — use with done(DELETED)

    def __init__(self, book_id: int, db_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._book_id     = book_id
        self._db_path     = db_path or DB_PATH
        self._book: dict  = {}   # populated by _load_book()
        self._file_renamed = False  # set True after a successful disk rename

        self.setWindowTitle("Book Details")
        self.setMinimumWidth(580)
        self.setModal(True)
        self.resize(620, 540)

        self._build_ui()
        self._load_book()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        form = self._build_form()
        outer.addLayout(form)
        outer.addStretch()

        outer.addLayout(self._build_buttons())

    def _build_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        # Filename — read-only display + Rename button
        filename_row = QHBoxLayout()
        self._filename = QLineEdit()
        self._filename.setReadOnly(True)
        self._filename.setStyleSheet("color: palette(mid);")
        self._filename.setToolTip("Current filename on disk")
        filename_row.addWidget(self._filename)

        self._rename_btn = QPushButton("Rename…")
        self._rename_btn.setToolTip("Rename the file on disk and update the database")
        self._rename_btn.setFixedWidth(90)
        self._rename_btn.clicked.connect(self._on_rename_clicked)
        filename_row.addWidget(self._rename_btn)

        form.addRow("Filename:", filename_row)

        # Title
        self._title = QLineEdit()
        self._title.setPlaceholderText("Required")
        form.addRow("Title *:", self._title)

        # Author
        self._author = QLineEdit()
        form.addRow("Author:", self._author)

        # Year — narrow field
        year_row = QHBoxLayout()
        self._year = QLineEdit()
        self._year.setPlaceholderText("e.g. 2021")
        self._year.setFixedWidth(90)
        year_row.addWidget(self._year)
        year_row.addStretch()
        form.addRow("Year:", year_row)

        # Language
        self._language = QLineEdit()
        self._language.setPlaceholderText("e.g. English")
        form.addRow("Language:", self._language)

        # Category
        self._category = QComboBox()
        self._category.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._category.addItem("")
        self._category.addItems(sorted(_TAXONOMY.keys()))
        self._category.currentTextChanged.connect(self._on_category_changed)
        form.addRow("Category:", self._category)

        # Subcategory — repopulated whenever category changes
        self._subcategory = QComboBox()
        self._subcategory.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addRow("Subcategory:", self._subcategory)

        # Difficulty
        self._difficulty = QComboBox()
        self._difficulty.addItems(_DIFFICULTY_OPTIONS)
        form.addRow("Difficulty:", self._difficulty)

        # Reading status
        self._reading_status = QComboBox()
        self._reading_status.addItems(_READING_STATUS_OPTIONS)
        form.addRow("Reading status:", self._reading_status)

        # Description
        self._description = QTextEdit()
        self._description.setPlaceholderText("Brief description of the book…")
        self._description.setFixedHeight(90)
        self._description.setAcceptRichText(False)
        form.addRow("Description:", self._description)

        # Tags
        self._tags = QLineEdit()
        self._tags.setPlaceholderText("comma-separated  e.g. python, data science, tutorial")
        form.addRow("Tags:", self._tags)

        return form

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.setStyleSheet("QPushButton#deleteButton { color: #c0392b; }")
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        row.addWidget(self._delete_btn)

        row.addStretch()

        self._open_btn = QPushButton("Open")
        self._open_btn.setToolTip("Open this file with the default Windows application")
        self._open_btn.clicked.connect(self._on_open_clicked)
        row.addWidget(self._open_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._on_save_clicked)
        row.addWidget(self._save_btn)

        return row

    # ------------------------------------------------------------------
    # Category → Subcategory cascade
    # ------------------------------------------------------------------

    def _on_category_changed(self, category: str) -> None:
        current_sub = self._subcategory.currentText()
        self._subcategory.blockSignals(True)
        self._subcategory.clear()
        self._subcategory.addItem("")
        self._subcategory.addItems(_TAXONOMY.get(category, []))
        # Restore previous selection if it still belongs to the new category
        idx = self._subcategory.findText(current_sub)
        self._subcategory.setCurrentIndex(idx if idx >= 0 else 0)
        self._subcategory.blockSignals(False)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _load_book(self) -> None:
        try:
            conn = get_conn(self._db_path)
            row = conn.execute(
                "SELECT * FROM books WHERE id = ?", (self._book_id,)
            ).fetchone()
        except Exception as exc:
            log.exception("DB error loading book id=%d", self._book_id)
            QMessageBox.critical(self, "Database Error",
                                 f"Could not load book:\n\n{exc}")
            self.reject()
            return

        if not row:
            QMessageBox.critical(self, "Not Found",
                                 f"Book id={self._book_id} was not found in the database.")
            self.reject()
            return

        self._book = dict(row)
        # Deserialize tags JSON → Python list once; stored back as list in self._book
        try:
            self._book["tags"] = json.loads(self._book.get("tags") or "[]")
        except (json.JSONDecodeError, TypeError):
            self._book["tags"] = []

        self._populate_form()

    def _populate_form(self) -> None:
        b = self._book

        self._filename.setText(b.get("filename") or "")
        self._title.setText(b.get("title") or "")
        self._author.setText(b.get("author") or "")
        self._year.setText(str(b.get("year") or ""))
        self._language.setText(b.get("language") or "")

        # Category must be set before subcategory so _on_category_changed
        # populates the subcategory list before we call setCurrentIndex on it.
        cat = b.get("category") or ""
        cat_idx = self._category.findText(cat)
        self._category.setCurrentIndex(cat_idx if cat_idx >= 0 else 0)
        # Trigger subcategory population even if index didn't change
        self._on_category_changed(cat)

        sub = b.get("subcategory") or ""
        sub_idx = self._subcategory.findText(sub)
        self._subcategory.setCurrentIndex(sub_idx if sub_idx >= 0 else 0)

        diff = b.get("difficulty") or ""
        diff_idx = self._difficulty.findText(diff)
        self._difficulty.setCurrentIndex(diff_idx if diff_idx >= 0 else 0)

        rs = b.get("reading_status") or "to-read"
        rs_idx = self._reading_status.findText(rs)
        self._reading_status.setCurrentIndex(rs_idx if rs_idx >= 0 else 0)

        self._description.setPlainText(b.get("description") or "")
        self._tags.setText(", ".join(b.get("tags") or []))

        display = b.get("title") or b.get("filename") or f"Book #{self._book_id}"
        self.setWindowTitle(f"Edit — {display}")

        # Enable Open only when there is a filepath we can hand to os.startfile
        filepath = b.get("filepath") or ""
        self._open_btn.setEnabled(bool(filepath))
        if not filepath:
            self._open_btn.setToolTip("No file path stored for this book")

    # ------------------------------------------------------------------
    # Open in default application
    # ------------------------------------------------------------------

    def _on_open_clicked(self) -> None:
        filepath = self._book.get("filepath") or ""
        if not filepath:
            QMessageBox.warning(self, "Cannot Open",
                                "No file path is stored for this book.")
            return

        # Normalise forward-slashes → OS path separators
        path = os.path.normpath(filepath)

        if not os.path.exists(path):
            QMessageBox.warning(
                self, "File Not Found",
                f"The file could not be found on disk:\n\n{path}",
            )
            return

        try:
            os.startfile(path)   # Windows: opens with the registered default app
            log.info("Opened book id=%d: %s", self._book_id, path)
        except OSError as exc:
            log.exception("os.startfile failed for %s", path)
            QMessageBox.critical(
                self, "Cannot Open File",
                f"Windows could not open the file:\n\n{exc}",
            )

    # ------------------------------------------------------------------
    # Rename file on disk
    # ------------------------------------------------------------------

    def _on_rename_clicked(self) -> None:
        from pathlib import Path  # noqa: PLC0415
        from ui.dialogs.rename_file_dialog import (  # noqa: PLC0415
            RenameFileDialog, generate_filename,
        )

        filepath = self._book.get("filepath") or ""
        if not filepath:
            QMessageBox.warning(self, "Cannot Rename",
                                "No file path is stored for this book.")
            return

        orig_path = Path(os.path.normpath(filepath))
        orig_ext  = orig_path.suffix

        suggested = generate_filename(
            self._book.get("title"),
            self._book.get("author"),
            self._book.get("year"),
            orig_ext,
        )

        dlg = RenameFileDialog(
            current_filename   = orig_path.name,
            suggested_filename = suggested,
            original_ext       = orig_ext,
            parent             = self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_name = dlg.get_new_filename()
        new_path = orig_path.parent / new_name

        if not orig_path.exists():
            QMessageBox.warning(self, "File Not Found",
                                f"The original file could not be found:\n{orig_path}")
            return

        if new_path.exists() and new_path != orig_path:
            QMessageBox.warning(self, "Name Conflict",
                                f'A file named "{new_name}" already exists in that folder.')
            return

        try:
            orig_path.rename(new_path)

            new_path_str = str(new_path).replace("\\", "/")
            conn = get_conn(self._db_path)
            conn.execute(
                "UPDATE books SET filename=?, filepath=? WHERE id=?",
                (new_name, new_path_str, self._book_id),
            )
            conn.commit()

            # Reflect the change in the in-memory book dict and the UI
            self._book["filename"] = new_name
            self._book["filepath"] = new_path_str
            self._filename.setText(new_name)
            self._file_renamed = True
            log.info("Renamed book id=%d: %s → %s", self._book_id, orig_path.name, new_name)

        except PermissionError:
            QMessageBox.critical(self, "Rename Failed",
                                 "Permission denied — the file may be open in another app.")
        except Exception as exc:
            log.exception("Rename failed for book id=%d", self._book_id)
            QMessageBox.critical(self, "Rename Failed", str(exc))

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _on_save_clicked(self) -> None:
        if not self._validate():
            return

        updates = self._collect_edits()
        if not updates:
            # Nothing changed — treat as cancel so MainWindow doesn't re-fetch
            self.reject()
            return

        try:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [self._book_id]
            conn = get_conn(self._db_path)
            conn.execute(
                f"UPDATE books SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            log.info("Saved book id=%d  changed=%s", self._book_id, sorted(updates))
            self.accept()

        except Exception as exc:
            log.exception("Save failed for book id=%d", self._book_id)
            QMessageBox.critical(self, "Save Failed",
                                 f"Could not save changes:\n\n{exc}")

    def _validate(self) -> bool:
        """Return True when form data is acceptable; show a warning and return False otherwise."""
        title = self._title.text().strip()
        if not title:
            QMessageBox.warning(self, "Validation Error", "Title is required.")
            self._title.setFocus()
            return False

        year = self._year.text().strip()
        if year:
            if not year.isdigit() or not (1000 <= int(year) <= 2100):
                QMessageBox.warning(
                    self, "Validation Error",
                    "Year must be a 4-digit number between 1000 and 2100."
                )
                self._year.setFocus()
                return False

        return True

    def _collect_edits(self) -> dict:
        """Return only the fields whose values differ from the loaded book data."""
        b = self._book

        def _str_or_none(widget: QLineEdit | QComboBox) -> str | None:
            v = (widget.text() if isinstance(widget, QLineEdit)
                 else widget.currentText()).strip()
            return v if v else None

        # Serialize new tags once for both comparison and storage
        new_tags_list: list[str] = [
            t.strip() for t in self._tags.text().split(",") if t.strip()
        ]
        new_tags_json  = json.dumps(new_tags_list, ensure_ascii=True)
        old_tags_json  = json.dumps(b.get("tags") or [], ensure_ascii=True)

        candidates: dict[str, object] = {
            "title":          self._title.text().strip(),       # required; never None
            "author":         _str_or_none(self._author),
            "year":           _str_or_none(self._year),
            "language":       _str_or_none(self._language),
            "category":       _str_or_none(self._category),
            "subcategory":    _str_or_none(self._subcategory),
            "difficulty":     _str_or_none(self._difficulty),
            "reading_status": self._reading_status.currentText(),
            "description":    self._description.toPlainText().strip() or None,
        }

        updates: dict = {}

        for field, new_val in candidates.items():
            # Normalize empty strings from the DB to None for comparison
            old_val = b.get(field) or None
            if new_val != old_val:
                updates[field] = new_val

        if new_tags_json != old_tags_json:
            updates["tags"] = new_tags_json   # stored as JSON string

        return updates

    def reject(self) -> None:
        # If a file rename already happened the main window must refresh its
        # table row even if the user cancelled the metadata edit.
        if self._file_renamed:
            self.accept()
        else:
            super().reject()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete_clicked(self) -> None:
        display = (self._book.get("title")
                   or self._book.get("filename")
                   or f"Book #{self._book_id}")

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f'Remove "{display}" from the library?\n\n'
            "The file will NOT be deleted from disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,          # safe default
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = get_conn(self._db_path)
            conn.execute("DELETE FROM books WHERE id = ?", (self._book_id,))
            conn.commit()
            log.info("Deleted book id=%d", self._book_id)
            self.done(self.DELETED)

        except Exception as exc:
            log.exception("Delete failed for book id=%d", self._book_id)
            QMessageBox.critical(self, "Delete Failed",
                                 f"Could not delete book:\n\n{exc}")
