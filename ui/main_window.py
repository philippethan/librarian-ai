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

from PyQt6.QtCore import QModelIndex, QPoint, QRect, QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QPainter, QPolygon
from PyQt6.QtWidgets import (
    QApplication,
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
    QStackedWidget,
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
        self._col_filters: dict[int, set[str]] = {}

        # Feature state
        self._settings      = QSettings("LibrarianAI", "Desktop")
        self._col_visible   = [True] * len(_COLUMNS)   # per-column visibility
        self._view_mode     = "list"                    # "list" | "cards"
        self._covers_dir    = os.path.join("backend", "data", "covers")

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

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        def _tb(label, tip, slot):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        toolbar.addWidget(_tb("Scan Library",
            "Scan the Books directory for new PDF/EPUB files.",
            self._on_scan_clicked))

        toolbar.addWidget(self._make_separator())

        self._delete_sel_btn = _tb("Delete Selected",
            "Delete all selected books from the database AND from disk.",
            self._on_delete_selected)
        self._delete_sel_btn.setEnabled(False)
        self._delete_sel_btn.setStyleSheet("color: #c0392b;")
        toolbar.addWidget(self._delete_sel_btn)

        toolbar.addWidget(self._make_separator())

        self._cols_btn = _tb("Columns \u25bc",
            "Show or hide individual columns.",
            self._on_columns_menu)
        toolbar.addWidget(self._cols_btn)

        toolbar.addWidget(self._make_separator())

        self._view_list_btn = _tb("List",
            "Show books as a sortable list (table view).",
            lambda: self._set_view("list"))
        self._view_list_btn.setCheckable(True)
        self._view_list_btn.setChecked(True)
        toolbar.addWidget(self._view_list_btn)

        self._view_card_btn = _tb("Cards",
            "Show books as a card grid with cover thumbnails.",
            lambda: self._set_view("cards"))
        self._view_card_btn.setCheckable(True)
        toolbar.addWidget(self._view_card_btn)

        toolbar.addWidget(self._make_separator())

        toolbar.addWidget(_tb("A−", "Decrease font size (Ctrl+−).", self._zoom_out))
        self._zoom_label = QLabel("10 pt")
        self._zoom_label.setFixedWidth(38)
        toolbar.addWidget(self._zoom_label)
        toolbar.addWidget(_tb("A+", "Increase font size (Ctrl++).", self._zoom_in))

        toolbar.addWidget(self._make_separator())

        toolbar.addWidget(_tb("Settings",
            "Application settings (display font, Khmer font, …).",
            self._open_settings))

        toolbar.addStretch()
        vbox.addLayout(toolbar)

        # ── Search bar ────────────────────────────────────────────────────
        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by filename, title or author…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_all_filters)
        hbox.addWidget(self._search)
        vbox.addLayout(hbox)

        # ── Stacked view (table | cards) ──────────────────────────────────
        self._stack = QStackedWidget()

        # Index 0: list/table view
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(False)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)

        self._header = _FilterHeaderView(self._table)
        self._header.filter_requested.connect(self._on_filter_requested)
        self._table.setHorizontalHeader(self._header)
        self._configure_columns()
        self._stack.addWidget(self._table)   # index 0

        # Index 1: card/grid view
        from ui.views.card_view import CardView  # noqa: PLC0415
        self._card_view = CardView(self._covers_dir, parent=self)
        self._card_view.book_activated.connect(self._open_book_detail)
        self._card_view.context_menu_requested.connect(self._on_card_context_menu)
        self._stack.addWidget(self._card_view)  # index 1

        vbox.addWidget(self._stack)

        # ── Status bar ────────────────────────────────────────────────────
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel("Loading…")
        sb.addWidget(self._status_label)

        # Apply saved font size to zoom label
        saved_size = self._settings.value("display/font_size", 10, type=int)
        self._zoom_label.setText(f"{saved_size} pt")

    def _configure_columns(self) -> None:
        widths = [300, 340, 190, 55, 155, 95]
        for i, w in enumerate(widths):
            self._header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(i, w)

    @staticmethod
    def _make_separator() -> QWidget:
        """Thin vertical line used as toolbar separator."""
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        sep.setStyleSheet("background: #ccc;")
        return sep

    # ------------------------------------------------------------------
    # Feature: column visibility
    # ------------------------------------------------------------------

    def _on_columns_menu(self) -> None:
        menu = QMenu(self)
        for col, (label, _) in enumerate(_COLUMNS):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._col_visible[col])
            action.setData(col)
        chosen = menu.exec(
            self._cols_btn.mapToGlobal(
                self._cols_btn.rect().bottomLeft()
            )
        )
        if chosen is not None:
            col = chosen.data()
            self._col_visible[col] = chosen.isChecked()
            self._table.setColumnHidden(col, not chosen.isChecked())

    # ------------------------------------------------------------------
    # Feature: view mode (list / cards)
    # ------------------------------------------------------------------

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        if mode == "list":
            self._stack.setCurrentIndex(0)
            self._view_list_btn.setChecked(True)
            self._view_card_btn.setChecked(False)
        else:
            self._stack.setCurrentIndex(1)
            self._view_list_btn.setChecked(False)
            self._view_card_btn.setChecked(True)
            # Repopulate cards with the currently visible books
            self._refresh_card_view()

    def _refresh_card_view(self) -> None:
        """Populate the card view with the books currently passing all filters."""
        visible = self._get_visible_books()
        self._card_view.populate(visible)

    def _get_visible_books(self) -> list[BookRow]:
        """Return the subset of _all_books that pass the current search + column filters."""
        query = self._search.text().strip().lower()
        result = []
        for book in self._all_books:
            vals = book.cell_values()
            if query:
                if not any(query in vals[c].lower() for c in _SEARCH_COLS):
                    continue
            col_ok = True
            for col, allowed in self._col_filters.items():
                if vals[col] not in allowed:
                    col_ok = False
                    break
            if col_ok:
                result.append(book)
        return result

    # ------------------------------------------------------------------
    # Feature: font zoom
    # ------------------------------------------------------------------

    def _zoom_in(self) -> None:
        self._apply_zoom(+1)

    def _zoom_out(self) -> None:
        self._apply_zoom(-1)

    def _apply_zoom(self, delta: int) -> None:
        app  = QApplication.instance()
        font = app.font()
        new_size = max(8, min(18, font.pointSize() + delta))
        font.setPointSize(new_size)
        app.setFont(font)
        self._zoom_label.setText(f"{new_size} pt")
        self._settings.setValue("display/font_size", new_size)

    # ------------------------------------------------------------------
    # Feature: settings dialog
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        from ui.dialogs.settings_dialog import SettingsDialog  # noqa: PLC0415
        dlg = SettingsDialog(parent=self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self) -> None:
        from ui.dialogs.settings_dialog import apply_settings_to_app  # noqa: PLC0415
        apply_settings_to_app(QApplication.instance())
        size = QApplication.instance().font().pointSize()
        self._zoom_label.setText(f"{size} pt")
        self._settings.setValue("display/font_size", size)

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
    # Scan
    # ------------------------------------------------------------------

    def _on_scan_clicked(self) -> None:
        """Open the scan dialog and refresh the table when it closes."""
        from ui.scan.scan_dialog import ScanProgressDialog  # noqa: PLC0415
        books_path = os.environ.get("BOOKS_PATH", "C:/Users/posen/Documents/Books")
        dlg = ScanProgressDialog(self._db_path, books_path, parent=self)
        accepted = dlg.exec()
        if accepted:
            self._load_books()
            self._status_label.setText("Scan complete — library refreshed.")

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
        if self._col_filters or self._search.text().strip():
            self._apply_all_filters()
        elif self._view_mode == "cards":
            self._refresh_card_view()

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
            if search_query:
                search_ok = any(
                    search_query in
                    (self._table.item(row, c) or QTableWidgetItem()).text().lower()
                    for c in _SEARCH_COLS
                )
            else:
                search_ok = True

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

        if self._view_mode == "cards":
            self._refresh_card_view()

    # ------------------------------------------------------------------
    # Selection tracking
    # ------------------------------------------------------------------

    def _on_table_selection_changed(self) -> None:
        n = len(self._table.selectedItems()) // len(_COLUMNS)
        self._delete_sel_btn.setEnabled(n > 0)
        self._delete_sel_btn.setText(
            f"Delete Selected ({n})" if n > 1 else "Delete Selected"
        )

    def _selected_book_ids(self) -> list[int]:
        """Return unique book IDs for all currently selected table rows."""
        seen: set[int] = set()
        ids: list[int] = []
        for item in self._table.selectedItems():
            if item.column() != 0:
                continue
            bid: int | None = item.data(Qt.ItemDataRole.UserRole)
            if bid is not None and bid not in seen:
                seen.add(bid)
                ids.append(bid)
        return ids

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos: QPoint) -> None:
        clicked_row = self._table.rowAt(pos.y())
        if clicked_row < 0:
            return

        # If the right-clicked row isn't in the current selection, replace selection
        selected_rows = {idx.row() for idx in self._table.selectedIndexes()}
        if clicked_row not in selected_rows:
            self._table.selectRow(clicked_row)

        ids = self._selected_book_ids()
        if not ids:
            return

        menu = QMenu(self)
        if len(ids) == 1:
            book_id = ids[0]
            menu.addAction("Edit Metadata…",
                           lambda: self._open_book_detail(book_id))
            menu.addAction("Rename File…",
                           lambda: self._on_rename_file(book_id))
            menu.addSeparator()

        del_action: QAction = menu.addAction(
            f"Delete {len(ids)} book(s) from DB + Disk…" if len(ids) > 1
            else "Delete from DB + Disk…"
        )
        del_action.setData(ids)
        del_action.triggered.connect(lambda: self._on_delete_selected())
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_card_context_menu(self, book_ids: list[int], global_pos: QPoint) -> None:
        menu = QMenu(self)
        if len(book_ids) == 1:
            bid = book_ids[0]
            menu.addAction("Edit Metadata…", lambda: self._open_book_detail(bid))
            menu.addSeparator()
        del_label = (f"Delete {len(book_ids)} books from DB + Disk…"
                     if len(book_ids) > 1 else "Delete from DB + Disk…")
        menu.addAction(del_label, lambda: self._delete_books(book_ids))
        menu.exec(global_pos)

    # ------------------------------------------------------------------
    # Multi-select delete (DB + disk)
    # ------------------------------------------------------------------

    def _on_delete_selected(self) -> None:
        ids = self._selected_book_ids()
        if not ids:
            return
        self._delete_books(ids)

    def _delete_books(self, book_ids: list[int]) -> None:
        """Delete one or more books from the database AND from disk after confirmation."""
        if not book_ids:
            return

        # Fetch book details for confirmation dialog
        conn = get_conn(self._db_path)
        placeholders = ",".join("?" * len(book_ids))
        rows = conn.execute(
            f"SELECT id, filename, filepath FROM books WHERE id IN ({placeholders})",
            book_ids,
        ).fetchall()
        if not rows:
            return

        books = [dict(r) for r in rows]
        n = len(books)

        # Build confirmation message
        if n == 1:
            detail = f'"{books[0]["filename"]}"'
        else:
            names  = "\n".join(f'  • {b["filename"]}' for b in books[:10])
            more   = f"\n  … and {n - 10} more" if n > 10 else ""
            detail = f"{n} books:\n{names}{more}"

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Permanently delete {detail}\n\n"
            "This will remove the record from the database AND delete the file(s) from disk.\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        errors: list[str] = []
        deleted_ids: list[int] = []

        for book in books:
            bid      = book["id"]
            filepath = (book.get("filepath") or "").strip()
            try:
                # Delete file from disk first
                if filepath:
                    norm = os.path.normpath(filepath)
                    if os.path.exists(norm):
                        os.remove(norm)
                        log.info("Deleted file: %s", norm)
                    else:
                        log.warning("File not found on disk (skipping): %s", norm)
                # Delete from DB
                conn = get_conn(self._db_path)
                conn.execute("DELETE FROM books WHERE id=?", (bid,))
                conn.commit()
                deleted_ids.append(bid)
                log.info("Deleted book id=%d from DB", bid)
            except PermissionError:
                errors.append(f"{book['filename']}: file is open in another application")
            except Exception as exc:
                log.exception("Delete failed for book id=%d", bid)
                errors.append(f"{book['filename']}: {exc}")

        # Remove from in-memory list + table + card view
        for bid in deleted_ids:
            self._all_books = [b for b in self._all_books if b.id != bid]
            for table_row in range(self._table.rowCount()):
                first = self._table.item(table_row, 0)
                if first and first.data(Qt.ItemDataRole.UserRole) == bid:
                    self._table.removeRow(table_row)
                    break

        if self._view_mode == "cards":
            self._refresh_card_view()

        self._update_status()
        self._status_label.setText(f"Deleted {len(deleted_ids)} book(s).")

        if errors:
            QMessageBox.warning(
                self, "Some Deletions Failed",
                "The following files could not be deleted:\n\n"
                + "\n".join(errors),
            )

    # ------------------------------------------------------------------
    # Single-book delete (legacy path — kept for row-level helpers)
    # ------------------------------------------------------------------

    def _on_delete_book(self, book_id: int) -> None:
        """Single-book delete — routes through the shared multi-delete path."""
        self._delete_books([book_id])

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
        """Remove a row from the table, the card view, and the in-memory list."""
        self._all_books = [b for b in self._all_books if b.id != book_id]
        for table_row in range(self._table.rowCount()):
            first = self._table.item(table_row, 0)
            if first and first.data(Qt.ItemDataRole.UserRole) == book_id:
                self._table.removeRow(table_row)
                break
        if self._view_mode == "cards":
            self._refresh_card_view()
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
