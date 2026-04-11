"""
ui/main_window.py — Main application window.

Displays all books in a sortable, filterable table.  Books are loaded from
SQLite in a background QThread so the UI never blocks on startup.
"""

import logging
import os
from typing import NamedTuple

from PyQt6.QtCore import QModelIndex, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.db import get_conn

log = logging.getLogger(__name__)

# Same default as backend/app.py — override via DB_PATH env var.
DB_PATH: str = os.environ.get("DB_PATH", "backend/data/librarian.db")

# (header label, BookRow field name)
_COLUMNS: list[tuple[str, str]] = [
    ("Filename", "filename"),
    ("Title",    "title"),
    ("Author",   "author"),
    ("Year",     "year"),
    ("Category", "category"),
    ("Status",   "status"),
]

# Columns searched by the filter bar (indices into _COLUMNS)
_SEARCH_COLS = (0, 1, 2)  # filename, title, author


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

class BookRow(NamedTuple):
    id:       int
    filename: str
    title:    str
    author:   str
    year:     str
    category: str
    status:   str

    def cell_values(self) -> tuple[str, ...]:
        return (self.filename, self.title, self.author,
                self.year, self.category, self.status)


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _BookLoaderThread(QThread):
    """Fetches every book row from SQLite and emits the result."""

    books_loaded   = pyqtSignal(list)   # list[BookRow]
    error_occurred = pyqtSignal(str)

    def __init__(self, db_path: str, parent=None) -> None:
        super().__init__(parent)
        self._db_path = db_path

    def run(self) -> None:
        try:
            conn = get_conn(self._db_path)
            rows = conn.execute(
                "SELECT id, filename, title, author, year, category, status "
                "FROM books ORDER BY filename COLLATE NOCASE"
            ).fetchall()

            books = [
                BookRow(
                    id       = r["id"],
                    filename = r["filename"] or "",
                    title    = r["title"]    or "",
                    author   = r["author"]   or "",
                    year     = str(r["year"]) if r["year"] else "",
                    category = r["category"] or "",
                    status   = r["status"]   or "",
                )
                for r in rows
            ]
            log.info("Loader: fetched %d books", len(books))
            self.books_loaded.emit(books)

        except Exception as exc:
            log.exception("Loader thread failed")
            self.error_occurred.emit(str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self._db_path  = db_path or DB_PATH
        self._all_books: list[BookRow] = []
        self._loader: _BookLoaderThread | None = None

        self._build_ui()
        self._load_books()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("LibrarianAI")
        self.setMinimumSize(900, 580)
        self.resize(1240, 780)

        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(8, 8, 8, 4)
        vbox.setSpacing(6)

        # Search bar
        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by filename, title or author…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        hbox.addWidget(self._search)
        vbox.addLayout(hbox)

        # Table
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        # Sorting is enabled after the table is fully populated (see _populate)
        self._table.setSortingEnabled(False)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._configure_columns()
        vbox.addWidget(self._table)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel("Loading…")
        sb.addWidget(self._status_label)

    def _configure_columns(self) -> None:
        """Set fixed column widths — avoids the O(n) cost of ResizeToContents."""
        h = self._table.horizontalHeader()
        widths = [300, 0, 190, 55, 155, 95]   # 0 = stretch for Title
        modes  = [
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.Stretch,
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.Fixed,
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.Fixed,
        ]
        for i, (w, mode) in enumerate(zip(widths, modes)):
            h.setSectionResizeMode(i, mode)
            if w:
                self._table.setColumnWidth(i, w)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_books(self) -> None:
        self._status_label.setText("Loading books…")
        self._loader = _BookLoaderThread(self._db_path, parent=self)
        self._loader.books_loaded.connect(self._on_books_loaded)
        self._loader.error_occurred.connect(self._on_load_error)
        self._loader.start()

    def _on_books_loaded(self, books: list[BookRow]) -> None:
        self._all_books = books
        self._populate_table(books)

    def _on_load_error(self, message: str) -> None:
        self._status_label.setText("Error loading books")
        log.error("Load error: %s", message)
        QMessageBox.critical(
            self,
            "Database Error",
            f"Could not load books from:\n{self._db_path}\n\n{message}",
        )

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self, books: list[BookRow]) -> None:
        """Fill the table from *books*.  Sorting is re-enabled afterwards."""
        self._table.setSortingEnabled(False)
        self._table.clearContents()
        self._table.setRowCount(len(books))

        for row_idx, book in enumerate(books):
            for col_idx, value in enumerate(book.cell_values()):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col_idx == 0:
                    # Attach the book id to column 0 for later retrieval
                    item.setData(Qt.ItemDataRole.UserRole, book.id)
                self._table.setItem(row_idx, col_idx, item)

        self._table.setSortingEnabled(True)
        self._update_status(len(books), len(books))

    # ------------------------------------------------------------------
    # Search / filter
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        query = text.strip().lower()
        visible = 0

        for row in range(self._table.rowCount()):
            if not query:
                hidden = False
            else:
                hidden = not any(
                    query in (self._table.item(row, col) or QTableWidgetItem()).text().lower()
                    for col in _SEARCH_COLS
                )
            self._table.setRowHidden(row, hidden)
            if not hidden:
                visible += 1

        self._update_status(visible, len(self._all_books))

    # ------------------------------------------------------------------
    # Row interaction
    # ------------------------------------------------------------------

    def _on_row_double_clicked(self, index: QModelIndex) -> None:
        row = index.row()
        item = self._table.item(row, 0)
        if item is None:
            return
        book_id: int | None = item.data(Qt.ItemDataRole.UserRole)
        if book_id is None:
            return
        self._open_book_detail(book_id)

    def _open_book_detail(self, book_id: int) -> None:
        try:
            from ui.book_detail_dialog import BookDetailDialog  # noqa: PLC0415
            dialog = BookDetailDialog(book_id, self._db_path, parent=self)
            if dialog.exec():
                self._refresh_book_row(book_id)
        except ImportError:
            QMessageBox.information(
                self,
                "Coming soon",
                "BookDetailDialog hasn't been built yet.",
            )
        except Exception as exc:
            log.exception("Error opening detail for book id=%d", book_id)
            QMessageBox.critical(self, "Error", str(exc))

    def _refresh_book_row(self, book_id: int) -> None:
        """Re-fetch one row from the DB and update the table in place."""
        try:
            conn = get_conn(self._db_path)
            r = conn.execute(
                "SELECT id, filename, title, author, year, category, status "
                "FROM books WHERE id = ?",
                (book_id,),
            ).fetchone()
            if not r:
                return

            updated = BookRow(
                id       = r["id"],
                filename = r["filename"] or "",
                title    = r["title"]    or "",
                author   = r["author"]   or "",
                year     = str(r["year"]) if r["year"] else "",
                category = r["category"] or "",
                status   = r["status"]   or "",
            )

            # Keep in-memory list consistent
            self._all_books = [
                updated if b.id == book_id else b for b in self._all_books
            ]

            # Find the visual row (position may differ from insertion order
            # because the user may have sorted the table)
            for table_row in range(self._table.rowCount()):
                first = self._table.item(table_row, 0)
                if first and first.data(Qt.ItemDataRole.UserRole) == book_id:
                    for col, value in enumerate(updated.cell_values()):
                        cell = self._table.item(table_row, col)
                        if cell:
                            cell.setText(value)
                    break

        except Exception:
            log.exception("Failed to refresh row for book id=%d", book_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_status(self, visible: int, total: int) -> None:
        if visible == total:
            self._status_label.setText(f"{total:,} books")
        else:
            self._status_label.setText(f"{visible:,} of {total:,} books")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._loader and self._loader.isRunning():
            log.debug("Waiting for loader thread to finish…")
            self._loader.quit()
            self._loader.wait(2_000)
        super().closeEvent(event)
