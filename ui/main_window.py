"""
ui/main_window.py — Main application window.

Features
--------
* Sortable QTableWidget showing all books
* Global search bar (filename / title / author)
* Per-column filter dropdowns in each header  (▼ arrow, Excel-style)
* Right-click context menu: Edit Metadata | Rename File | Delete
* Delete confirmation dialog with optional "also delete from disk"
* Double-click a row to open BookDetailDialog
* Status bar: book count + active filter summary
"""

import logging
import os
from typing import NamedTuple

from PyQt6.QtCore import QModelIndex, QPoint, QRect, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QPainter, QPolygon
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.db import get_conn

log = logging.getLogger(__name__)

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

# Columns matched by the global search bar
_SEARCH_COLS = (0, 1, 2)   # filename, title, author

# Width (px) reserved for the filter-arrow in each column header
_ARROW_W = 22


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
    books_loaded   = pyqtSignal(list)
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
# Custom header view — draws a ▼ filter arrow in every column section
# ---------------------------------------------------------------------------

class _FilterHeaderView(QHeaderView):
    """Horizontal header that paints a small ▼ arrow on the right of each
    section.  Clicking the arrow area opens a per-column filter popup;
    clicking elsewhere sorts as normal.
    """

    filter_requested = pyqtSignal(int)   # column logical index

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._filtered: set[int] = set()   # columns with an active filter
        self.setSectionsClickable(True)
        self.setHighlightSections(True)

    # -- public API --------------------------------------------------------

    def set_filtered(self, col: int, active: bool) -> None:
        if active:
            self._filtered.add(col)
        else:
            self._filtered.discard(col)
        self.viewport().update()

    def is_filtered(self, col: int) -> bool:
        return col in self._filtered

    # -- painting ----------------------------------------------------------

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:  # noqa: N802
        painter.save()
        super().paintSection(painter, rect, logical_index)
        painter.restore()

        # Draw the filter arrow on the right edge of the section
        active = logical_index in self._filtered
        self._paint_arrow(painter, rect, active)

    def _paint_arrow(self, painter: QPainter, rect: QRect, active: bool) -> None:
        arrow_rect = QRect(rect.right() - _ARROW_W, rect.top(), _ARROW_W, rect.height())
        cx = arrow_rect.center().x()
        cy = arrow_rect.center().y()
        half = 4

        color = QColor("#1a6faf") if active else QColor("#999999")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        # Down-pointing triangle
        triangle = QPolygon([
            QPoint(cx - half, cy - 2),
            QPoint(cx + half, cy - 2),
            QPoint(cx,        cy + 3),
        ])
        painter.drawPolygon(triangle)
        painter.restore()

    # -- mouse interaction -------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos  = event.pos()
        logi = self.logicalIndexAt(pos)
        if logi < 0:
            super().mousePressEvent(event)
            return

        sec_start = self.sectionViewportPosition(logi)
        sec_width = self.sectionSize(logi)
        arrow_x   = sec_start + sec_width - _ARROW_W

        if pos.x() >= arrow_x:
            # Arrow zone → open filter; do NOT propagate to avoid sorting
            self.filter_requested.emit(logi)
        else:
            # Normal header zone → sort
            super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Per-column filter popup
# ---------------------------------------------------------------------------

class _ColumnFilterDialog(QDialog):
    """Checkbox list of unique values for one column.

    The 'all_values' set is the full universe of values currently in the
    table for that column.  'selected' is the subset currently allowed
    (None means "all selected" = no filter).
    """

    def __init__(
        self,
        column_name: str,
        all_values:  list[str],
        selected:    set[str] | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Filter — {column_name}")
        self.setMinimumWidth(300)
        self.resize(320, 420)
        self.setModal(True)
        self._all = all_values
        self._init_selected = selected if selected is not None else set(all_values)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(6)

        # Search box to filter the checkbox list
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search values…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_list)
        outer.addWidget(self._search)

        # All / None shortcuts
        shortcuts = QHBoxLayout()
        btn_all  = QPushButton("All")
        btn_none = QPushButton("None")
        btn_all.clicked.connect(self._select_all)
        btn_none.clicked.connect(self._select_none)
        shortcuts.addWidget(btn_all)
        shortcuts.addWidget(btn_none)
        shortcuts.addStretch()
        outer.addLayout(shortcuts)

        # Checkbox list
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for val in self._all:
            item = QListWidgetItem(val if val else "(empty)")
            item.setData(Qt.ItemDataRole.UserRole, val)
            item.setCheckState(
                Qt.CheckState.Checked
                if val in self._init_selected
                else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
        outer.addWidget(self._list)

        # Apply / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        apply_btn = QPushButton("Apply")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self.accept)
        btn_row.addWidget(apply_btn)
        outer.addLayout(btn_row)

    # -- helpers -----------------------------------------------------------

    def _filter_list(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _select_all(self) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Checked)

    def _select_none(self) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)

    # -- public API --------------------------------------------------------

    def get_selected(self) -> set[str]:
        result: set[str] = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.add(item.data(Qt.ItemDataRole.UserRole))
        return result


# ---------------------------------------------------------------------------
# Delete confirmation dialog
# ---------------------------------------------------------------------------

class _DeleteConfirmDialog(QDialog):
    """Shows book details and an optional 'delete from disk' checkbox."""

    def __init__(self, book: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Delete")
        self.setModal(True)
        self.setMinimumWidth(400)
        self._build_ui(book)

    def _build_ui(self, book: dict) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(6)

        outer.addWidget(QLabel(
            f"<b>Filename:</b>  {book.get('filename') or '—'}"
        ))
        outer.addWidget(QLabel(
            f"<b>Title:</b>  {book.get('title') or '—'}"
        ))
        outer.addWidget(QLabel(
            f"<b>Author:</b>  {book.get('author') or '—'}"
        ))

        outer.addSpacing(8)

        self._delete_file_cb = QCheckBox("Also delete file from disk")
        outer.addWidget(self._delete_file_cb)

        outer.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("confirmDeleteBtn")
        delete_btn.setStyleSheet(
            "QPushButton#confirmDeleteBtn { color: #c0392b; font-weight: bold; }"
        )
        delete_btn.setDefault(True)
        delete_btn.clicked.connect(self.accept)
        btn_row.addWidget(delete_btn)

        outer.addLayout(btn_row)

    def should_delete_file(self) -> bool:
        return self._delete_file_cb.isChecked()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self._db_path    = db_path or DB_PATH
        self._all_books: list[BookRow] = []
        self._loader: _BookLoaderThread | None = None
        # col_index -> frozenset of allowed values (absent = no filter on that col)
        self._col_filters: dict[int, set[str]] = {}

        self._build_ui()
        self._build_menu()
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

        # Global search bar
        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by filename, title or author…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_all_filters)
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
        self._table.setSortingEnabled(False)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        # Right-click context menu
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        # Install custom header with filter arrows
        self._header = _FilterHeaderView(self._table)
        self._header.filter_requested.connect(self._on_filter_requested)
        self._table.setHorizontalHeader(self._header)
        self._configure_columns()

        vbox.addWidget(self._table)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel("Loading…")
        sb.addWidget(self._status_label)

    def _configure_columns(self) -> None:
        widths = [300, 0, 190, 55, 155, 95]
        modes  = [
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.Stretch,
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.Fixed,
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.Fixed,
        ]
        for i, (w, mode) in enumerate(zip(widths, modes)):
            self._header.setSectionResizeMode(i, mode)
            if w:
                self._table.setColumnWidth(i, w)

    def _build_menu(self) -> None:
        mb = self.menuBar()
        lib_menu = mb.addMenu("&Library")
        lib_menu.addAction("&Manage Categories…", self._open_category_manager)
        lib_menu.addSeparator()
        lib_menu.addAction("&Clear All Column Filters", self._clear_all_col_filters)

    # ------------------------------------------------------------------
    # Category manager
    # ------------------------------------------------------------------

    def _open_category_manager(self) -> None:
        from ui.category_manager import CategoryManagerDialog  # noqa: PLC0415
        dlg = CategoryManagerDialog(self._db_path, parent=self)
        dlg.categories_updated.connect(self._load_books)
        dlg.exec()

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
        # Re-apply any active filters after a reload
        if self._col_filters or self._search.text().strip():
            self._apply_all_filters()

    def _on_load_error(self, message: str) -> None:
        self._status_label.setText("Error loading books")
        log.error("Load error: %s", message)
        QMessageBox.critical(self, "Database Error",
                             f"Could not load books:\n{self._db_path}\n\n{message}")

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self, books: list[BookRow]) -> None:
        self._table.setSortingEnabled(False)
        self._table.clearContents()
        self._table.setRowCount(len(books))

        for row_idx, book in enumerate(books):
            for col_idx, value in enumerate(book.cell_values()):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col_idx == 0:
                    item.setData(Qt.ItemDataRole.UserRole, book.id)
                self._table.setItem(row_idx, col_idx, item)

        self._table.setSortingEnabled(True)
        self._update_status()

    # ------------------------------------------------------------------
    # Per-column filter
    # ------------------------------------------------------------------

    def _on_filter_requested(self, col: int) -> None:
        """Open the column filter popup for *col*."""
        # Collect unique values currently in the table for this column
        all_vals: list[str] = sorted(
            {
                (self._table.item(r, col) or QTableWidgetItem()).text()
                for r in range(self._table.rowCount())
            },
            key=str.lower,
        )
        if not all_vals:
            return

        current_filter = self._col_filters.get(col)   # None = all selected
        dlg = _ColumnFilterDialog(
            _COLUMNS[col][0], all_vals, current_filter, parent=self
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.get_selected()
        all_set   = set(all_vals)

        if selected >= all_set:
            # Everything selected → clear filter for this column
            self._col_filters.pop(col, None)
            self._header.set_filtered(col, False)
        else:
            self._col_filters[col] = selected
            self._header.set_filtered(col, True)

        self._apply_all_filters()

    def _clear_all_col_filters(self) -> None:
        for col in list(self._col_filters):
            self._header.set_filtered(col, False)
        self._col_filters.clear()
        self._apply_all_filters()

    # ------------------------------------------------------------------
    # Combined filter (search bar + column filters)
    # ------------------------------------------------------------------

    def _apply_all_filters(self) -> None:
        """Show/hide rows based on the global search bar AND all column filters."""
        search_query = self._search.text().strip().lower()
        visible = 0

        for row in range(self._table.rowCount()):
            # 1. Global search (filename / title / author)
            if search_query:
                search_ok = any(
                    search_query in
                    (self._table.item(row, c) or QTableWidgetItem()).text().lower()
                    for c in _SEARCH_COLS
                )
            else:
                search_ok = True

            # 2. Per-column filters (AND logic — all must match)
            col_ok = True
            for col, allowed in self._col_filters.items():
                cell = self._table.item(row, col)
                val  = cell.text() if cell else ""
                if val not in allowed:
                    col_ok = False
                    break

            hidden = not (search_ok and col_ok)
            self._table.setRowHidden(row, hidden)
            if not hidden:
                visible += 1

        self._update_status(visible)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos: QPoint) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        first = self._table.item(row, 0)
        if not first:
            return
        book_id: int | None = first.data(Qt.ItemDataRole.UserRole)
        if book_id is None:
            return

        # Ensure the row is selected so the user sees what was right-clicked
        self._table.selectRow(row)

        menu = QMenu(self)
        menu.addAction("Edit Metadata…",
                       lambda: self._open_book_detail(book_id))
        menu.addAction("Rename File…",
                       lambda: self._on_rename_file(book_id))
        menu.addSeparator()
        delete_action: QAction = menu.addAction("Delete…")
        delete_action.triggered.connect(lambda: self._on_delete_book(book_id))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete_book(self, book_id: int) -> None:
        try:
            conn = get_conn(self._db_path)
            row = conn.execute(
                "SELECT filename, title, author, filepath FROM books WHERE id=?",
                (book_id,),
            ).fetchone()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))
            return

        if not row:
            return

        book = dict(row)
        dlg  = _DeleteConfirmDialog(book, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        delete_file = dlg.should_delete_file()

        try:
            if delete_file:
                filepath = book.get("filepath") or ""
                if filepath and os.path.exists(filepath):
                    os.remove(filepath)
                    log.info("Deleted file from disk: %s", filepath)
                elif filepath:
                    log.warning("File not found on disk (skipping): %s", filepath)

            conn = get_conn(self._db_path)
            conn.execute("DELETE FROM books WHERE id=?", (book_id,))
            conn.commit()
            log.info("Deleted book id=%d from DB", book_id)

            self._remove_book_row(book_id)
            self._status_label.setText(f"Deleted: {book.get('filename', '')}")

        except PermissionError:
            QMessageBox.critical(
                self, "Delete Failed",
                "Permission denied — the file may be open in another application.\n\n"
                "Close the file and try again.",
            )
        except Exception as exc:
            log.exception("Delete failed for book id=%d", book_id)
            QMessageBox.critical(self, "Delete Failed", str(exc))

    # ------------------------------------------------------------------
    # Rename file
    # ------------------------------------------------------------------

    def _on_rename_file(self, book_id: int) -> None:
        try:
            conn = get_conn(self._db_path)
            row  = conn.execute(
                "SELECT filename, filepath, title, author, year FROM books WHERE id=?",
                (book_id,),
            ).fetchone()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))
            return

        if not row:
            return

        book = dict(row)
        from pathlib import Path  # noqa: PLC0415
        from ui.dialogs.rename_file_dialog import (  # noqa: PLC0415
            RenameFileDialog, generate_filename,
        )

        orig_ext   = Path(book["filepath"] or book["filename"] or "").suffix
        suggested  = generate_filename(
            book["title"], book["author"], book["year"], orig_ext
        )
        dlg = RenameFileDialog(
            current_filename   = book["filename"] or "",
            suggested_filename = suggested,
            original_ext       = orig_ext,
            parent             = self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_name = dlg.get_new_filename()
        old_path = Path(book["filepath"]) if book["filepath"] else None

        if old_path is None:
            QMessageBox.warning(self, "Rename Failed",
                                "This book has no filepath stored in the database.")
            return

        new_path = old_path.parent / new_name

        if new_path.exists() and new_path != old_path:
            QMessageBox.warning(self, "Rename Failed",
                                f'A file named "{new_name}" already exists.')
            return

        try:
            old_path.rename(new_path)

            conn = get_conn(self._db_path)
            conn.execute(
                "UPDATE books SET filename=?, filepath=? WHERE id=?",
                (new_name, str(new_path).replace("\\", "/"), book_id),
            )
            conn.commit()
            log.info("Renamed book id=%d: %s → %s", book_id, old_path.name, new_name)

            self._refresh_book_row(book_id)
            self._status_label.setText(f"Renamed to: {new_name}")

        except PermissionError:
            QMessageBox.critical(self, "Rename Failed",
                                 "Permission denied — the file may be open in another app.")
        except Exception as exc:
            log.exception("Rename failed for book id=%d", book_id)
            QMessageBox.critical(self, "Rename Failed", str(exc))

    # ------------------------------------------------------------------
    # Double-click → edit metadata
    # ------------------------------------------------------------------

    def _on_row_double_clicked(self, index: QModelIndex) -> None:
        item = self._table.item(index.row(), 0)
        if item is None:
            return
        book_id: int | None = item.data(Qt.ItemDataRole.UserRole)
        if book_id is not None:
            self._open_book_detail(book_id)

    def _open_book_detail(self, book_id: int) -> None:
        try:
            from ui.book_detail_dialog import BookDetailDialog  # noqa: PLC0415
            dlg    = BookDetailDialog(book_id, self._db_path, parent=self)
            result = dlg.exec()
            if result == BookDetailDialog.DELETED:
                self._remove_book_row(book_id)
            elif result:
                self._refresh_book_row(book_id)
        except Exception as exc:
            log.exception("Error opening book detail id=%d", book_id)
            QMessageBox.critical(self, "Error", str(exc))

    # ------------------------------------------------------------------
    # Row-level helpers
    # ------------------------------------------------------------------

    def _refresh_book_row(self, book_id: int) -> None:
        """Re-fetch one row from the DB and update it in the table."""
        try:
            conn = get_conn(self._db_path)
            r = conn.execute(
                "SELECT id, filename, title, author, year, category, status "
                "FROM books WHERE id=?",
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
            self._all_books = [
                updated if b.id == book_id else b for b in self._all_books
            ]

            for table_row in range(self._table.rowCount()):
                first = self._table.item(table_row, 0)
                if first and first.data(Qt.ItemDataRole.UserRole) == book_id:
                    for col, value in enumerate(updated.cell_values()):
                        cell = self._table.item(table_row, col)
                        if cell:
                            cell.setText(value)
                    break
        except Exception:
            log.exception("Failed to refresh row book id=%d", book_id)

    def _remove_book_row(self, book_id: int) -> None:
        """Remove a row from the table and the in-memory list."""
        self._all_books = [b for b in self._all_books if b.id != book_id]
        for table_row in range(self._table.rowCount()):
            first = self._table.item(table_row, 0)
            if first and first.data(Qt.ItemDataRole.UserRole) == book_id:
                self._table.removeRow(table_row)
                break
        self._update_status()

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _update_status(self, visible: int | None = None) -> None:
        total = len(self._all_books)

        if visible is None:
            # Count non-hidden rows (called after populate, before filter runs)
            visible = sum(
                1 for r in range(self._table.rowCount())
                if not self._table.isRowHidden(r)
            )

        n_col_filters = len(self._col_filters)
        has_search    = bool(self._search.text().strip())

        parts: list[str] = []
        if visible != total:
            parts.append(f"{visible:,} of {total:,} books")
        else:
            parts.append(f"{total:,} books")

        if n_col_filters:
            parts.append(
                f"{n_col_filters} column filter"
                f"{'s' if n_col_filters > 1 else ''} active"
            )
        if has_search:
            parts.append("search active")

        self._status_label.setText("  |  ".join(parts))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._loader and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(2_000)
        super().closeEvent(event)
