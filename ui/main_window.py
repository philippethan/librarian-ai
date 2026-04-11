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

from PyQt6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSettings, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QImage, QPainter, QPixmap, QPixmapCache, QPolygon
from PyQt6.QtWidgets import (
    QAbstractButton,
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
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.db import get_conn

log = logging.getLogger(__name__)

DB_PATH: str = os.environ.get("DB_PATH", "backend/data/librarian.db")


class ColDef(NamedTuple):
    header:          str
    field:           str
    default_visible: bool
    width:           int
    filterable:      bool = True


_ALL_COLUMNS: list[ColDef] = [
    ColDef("Cover",          "cover",             True,   64, False),
    ColDef("Filename",       "filename",          True,  270, True),
    ColDef("Title",          "title",             True,  260, True),
    ColDef("Author",         "author",            True,  170, True),
    ColDef("Year",           "year",              True,   55, True),
    ColDef("Language",       "language",          False,  80, True),
    ColDef("Category",       "category",          True,  145, True),
    ColDef("Subcategory",    "subcategory",       False, 130, True),
    ColDef("Difficulty",     "difficulty",        False,  85, True),
    ColDef("Status",         "status",            True,   90, True),
    ColDef("Reading",        "reading_status",    False,  80, True),
    ColDef("File Type",      "file_type",         False,  65, True),
    ColDef("File Size",      "file_size",         False,  80, False),
    ColDef("Tags",           "tags",              False, 200, True),
    ColDef("Confidence",     "confidence_score",  False,  75, False),
    ColDef("Method",         "extraction_method", False, 115, True),
    ColDef("Added",          "added_at",          False, 125, True),
    ColDef("Processed",      "processed_at",      False, 125, True),
]

# Logical column indices searched by the global search bar
# Cover=0, Filename=1, Title=2, Author=3
_SEARCH_COLS = (1, 2, 3)

# Width (px) reserved for the filter-arrow in each column header
_ARROW_W = 22


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

class BookRow(NamedTuple):
    id:                int
    filename:          str
    title:             str
    author:            str
    year:              str
    language:          str
    category:          str
    subcategory:       str
    difficulty:        str
    status:            str
    reading_status:    str
    file_type:         str
    file_size:         str   # formatted e.g. "1.2 MB"
    tags:              str
    confidence_score:  str
    extraction_method: str
    added_at:          str
    processed_at:      str

    def get_field(self, field: str) -> str:
        """Return the value for a given _ALL_COLUMNS field name."""
        if field == "cover":
            return ""
        return getattr(self, field, "") or ""

    def cell_values(self) -> tuple[str, ...]:
        """Values in _ALL_COLUMNS order; index 0 (cover) is always empty string."""
        return (
            "",                     # 0: cover — icon handled by _CoverLoader
            self.filename,          # 1
            self.title,             # 2
            self.author,            # 3
            self.year,              # 4
            self.language,          # 5
            self.category,          # 6
            self.subcategory,       # 7
            self.difficulty,        # 8
            self.status,            # 9
            self.reading_status,    # 10
            self.file_type,         # 11
            self.file_size,         # 12
            self.tags,              # 13
            self.confidence_score,  # 14
            self.extraction_method, # 15
            self.added_at,          # 16
            self.processed_at,      # 17
        )


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
                """SELECT id, filename, title, author, year, language,
                          category, subcategory, difficulty, status,
                          reading_status, file_type, file_size, tags,
                          confidence_score, extraction_method, added_at, processed_at
                   FROM books ORDER BY filename COLLATE NOCASE"""
            ).fetchall()

            def _fmt_size(val) -> str:
                if not val:
                    return ""
                try:
                    return f"{int(val) / 1_048_576:.1f} MB"
                except (TypeError, ValueError):
                    return str(val)

            books = [
                BookRow(
                    id                = r["id"],
                    filename          = r["filename"]          or "",
                    title             = r["title"]             or "",
                    author            = r["author"]            or "",
                    year              = str(r["year"]) if r["year"] else "",
                    language          = r["language"]          or "",
                    category          = r["category"]          or "",
                    subcategory       = r["subcategory"]       or "",
                    difficulty        = r["difficulty"]        or "",
                    status            = r["status"]            or "",
                    reading_status    = r["reading_status"]    or "",
                    file_type         = r["file_type"]         or "",
                    file_size         = _fmt_size(r["file_size"]),
                    tags              = r["tags"]              or "",
                    confidence_score  = (f"{r['confidence_score']:.0%}"
                                         if r["confidence_score"] else ""),
                    extraction_method = r["extraction_method"] or "",
                    added_at          = (r["added_at"]    or "")[:10],
                    processed_at      = (r["processed_at"] or "")[:10],
                )
                for r in rows
            ]
            log.info("Loader: fetched %d books", len(books))
            self.books_loaded.emit(books)
        except Exception as exc:
            log.exception("Loader thread failed")
            self.error_occurred.emit(str(exc))


# ---------------------------------------------------------------------------
# Background cover loader
# ---------------------------------------------------------------------------

class _CoverLoader(QThread):
    """Loads cover thumbnails as QImage objects (thread-safe) in the background.

    Emits cover_loaded(book_id, QImage) for each cover found.
    The main thread converts QImage → QPixmap → QIcon and sets it on the row.
    """

    cover_loaded = pyqtSignal(int, object)   # book_id, QImage

    def __init__(self, books: list, covers_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._books      = books
        self._covers_dir = covers_dir
        self._cancelled  = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for book in self._books:
            if self._cancelled:
                break
            path = os.path.join(self._covers_dir, f"{book.id}.jpg")
            if os.path.exists(path):
                img = QImage(path)
                if not img.isNull():
                    # Scale to card image area (178×180 px) — large enough to
                    # look sharp in card view and still fine scaled down as a
                    # list-view icon.
                    img = img.scaled(
                        178, 180,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.cover_loaded.emit(book.id, img)


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
        self._filtered: set[int] = set()        # columns with an active filter
        self._non_filterable: set[int] = set()  # columns with no filter arrow
        self.setSectionsClickable(True)
        self.setHighlightSections(True)

    def set_non_filterable(self, cols: set[int]) -> None:
        self._non_filterable = cols
        self.viewport().update()

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

        # Only draw the filter arrow for filterable columns
        if logical_index not in self._non_filterable:
            active = logical_index in self._filtered
            self._paint_arrow(painter, rect, active)

    def _paint_arrow(self, painter: QPainter, rect: QRect, active: bool) -> None:
        arrow_rect = QRect(rect.right() - _ARROW_W, rect.top(), _ARROW_W, rect.height())
        cx = arrow_rect.center().x()
        cy = arrow_rect.center().y()
        half = 4

        from ui.theme import COLORS as _T  # noqa: PLC0415
        color = QColor(_T["accent"]) if active else QColor(_T["text_disabled"])
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

        # Non-filterable columns — only sorting, no arrow click
        if logi in self._non_filterable:
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
# Khmer-aware helpers
# ---------------------------------------------------------------------------

def _has_khmer(text: str) -> bool:
    """Return True if *text* contains any Khmer Unicode character (U+1780–U+17FF)."""
    return any("\u1780" <= ch <= "\u17ff" for ch in (text or ""))


def _doc_font(base_size: int, khmer_family: str, text: str, bold: bool = False) -> QFont:
    """
    Return the right QFont for a piece of document-info text.

    • Khmer text in title/author/filename → khmer_family (if set), else Segoe UI
    • Everything else                     → Segoe UI at base_size
    Bold is honoured for title cells.
    """
    if khmer_family and _has_khmer(text):
        f = QFont(khmer_family, base_size)
    else:
        f = QFont("Segoe UI", base_size)
    f.setBold(bold)
    return f


# ---------------------------------------------------------------------------
# Custom table delegate — doc-info font size + Khmer font per cell
# ---------------------------------------------------------------------------

# Column indices that may contain Khmer text (filename=1, title=2, author=3)
_KHMER_COLS = frozenset({1, 2, 3})


class _DocInfoDelegate(QStyledItemDelegate):
    """
    Applies document-information font settings to table cells:
      • doc_size  — point size for all book-info cells
      • khmer_font — family used when the cell text contains Khmer characters
                     (only for filename / title / author columns)

    All other columns use Segoe UI at doc_size.
    The rest of the UI (menus, toolbar, dialogs) is untouched.
    """

    def __init__(self, doc_size: int, khmer_font: str, parent=None) -> None:
        super().__init__(parent)
        self._doc_size   = doc_size
        self._khmer_font = khmer_font

    def update_settings(self, doc_size: int, khmer_font: str) -> None:
        self._doc_size   = doc_size
        self._khmer_font = khmer_font

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        col  = index.column()
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        bold = (col == 2)   # title column bold
        option.font = _doc_font(
            self._doc_size,
            self._khmer_font if col in _KHMER_COLS else "",
            text,
            bold,
        )


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self._db_path    = db_path or DB_PATH
        self._all_books: list[BookRow] = []
        self._loader: _BookLoaderThread | None = None
        self._cover_loader: _CoverLoader | None = None
        self._col_filters: dict[int, set[str]] = {}
        self._book_id_to_row: dict[int, int] = {}   # O(1) table-row lookup by book_id
        self._covers_loaded: int = 0
        self._covers_total:  int = 0

        # Feature state
        self._settings      = QSettings("LibrarianAI", "Desktop")
        self._col_visible   = [c.default_visible for c in _ALL_COLUMNS]
        self._view_mode     = "list"   # "list" | "cards" | "shelves"
        self._covers_dir    = os.path.join("backend", "data", "covers")

        # Raise the pixmap cache limit so card-view covers (≤178×180 px each)
        # don't evict each other — 100 MB comfortably holds 1 000+ books.
        QPixmapCache.setCacheLimit(102_400)  # kilobytes

        self._build_ui()
        self._build_menu()
        self._restore_window_state()
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

        self._view_shelves_btn = _tb("Shelves",
            "Show virtual shelves — create and browse book collections.",
            lambda: self._set_view("shelves"))
        self._view_shelves_btn.setCheckable(True)
        toolbar.addWidget(self._view_shelves_btn)

        toolbar.addWidget(self._make_separator())

        toolbar.addWidget(_tb("Settings",
            "Application and document display settings.",
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
        self._table = QTableWidget(0, len(_ALL_COLUMNS))
        self._table.setHorizontalHeaderLabels([c.header for c in _ALL_COLUMNS])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        # Vertical header — visible row numbers, Excel-style drag-to-resize
        vh = self._table.verticalHeader()
        vh.setVisible(True)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        vh.setMinimumSectionSize(20)
        vh.setDefaultSectionSize(64)
        # Double-click a row number → reset that row to default height
        vh.sectionDoubleClicked.connect(
            lambda idx: self._table.verticalHeader().resizeSection(idx, 64)
        )
        # Right-click the corner button → reset all rows
        corner = self._table.findChild(QAbstractButton)
        if corner:
            corner.setToolTip("Double-click to reset all row heights")
            corner.installEventFilter(self)
        self._table.setSortingEnabled(False)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._table.setIconSize(QSize(48, 60))

        # Doc-info delegate — applies doc font size + Khmer font per cell
        from ui.dialogs.settings_dialog import load_settings  # noqa: PLC0415
        _cfg = load_settings()
        self._doc_delegate = _DocInfoDelegate(
            _cfg["doc_font_size"], _cfg["khmer_font"], self._table
        )
        self._table.setItemDelegate(self._doc_delegate)

        self._header = _FilterHeaderView(self._table)
        self._header.filter_requested.connect(self._on_filter_requested)
        self._table.setHorizontalHeader(self._header)
        self._configure_columns()
        self._stack.addWidget(self._table)   # index 0

        # Index 1: card/grid view
        from ui.views.card_view import CardView  # noqa: PLC0415
        from ui.dialogs.settings_dialog import load_settings as _ls  # noqa: PLC0415
        _s = _ls()
        self._card_view = CardView(
            self._covers_dir,
            doc_size=_s["doc_font_size"],
            khmer_font=_s["khmer_font"],
            parent=self,
        )
        self._card_view.book_activated.connect(self._open_book_detail)
        self._card_view.context_menu_requested.connect(self._on_card_context_menu)
        self._card_view.itemSelectionChanged.connect(self._on_card_selection_changed)
        self._stack.addWidget(self._card_view)  # index 1

        # Index 2: shelves view
        from ui.views.shelf_view import ShelfView  # noqa: PLC0415
        self._shelf_view = ShelfView(self._db_path, self._covers_dir, parent=self)
        self._shelf_view.book_activated.connect(self._open_book_detail)
        self._stack.addWidget(self._shelf_view)  # index 2

        vbox.addWidget(self._stack)

        # ── Status bar ────────────────────────────────────────────────────
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel("Loading…")
        sb.addWidget(self._status_label)

    def _configure_columns(self) -> None:
        non_filterable = {i for i, c in enumerate(_ALL_COLUMNS) if not c.filterable}
        self._header.set_non_filterable(non_filterable)
        for i, col in enumerate(_ALL_COLUMNS):
            self._header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(i, col.width)
            if not col.default_visible:
                self._table.setColumnHidden(i, True)

    @staticmethod
    def _make_separator() -> QWidget:
        """Thin vertical line used as toolbar separator."""
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        sep.setStyleSheet("background: #253045;")
        return sep

    # ------------------------------------------------------------------
    # Feature: column visibility
    # ------------------------------------------------------------------

    def _on_columns_menu(self) -> None:
        menu = QMenu(self)
        for col, cdef in enumerate(_ALL_COLUMNS):
            action = menu.addAction(cdef.header)
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
            # When cover column re-enabled, repopulate table icons from QPixmapCache
            if col == 0 and chosen.isChecked():
                for row in range(self._table.rowCount()):
                    item = self._table.item(row, 0)
                    if not item:
                        continue
                    bid = item.data(Qt.ItemDataRole.UserRole)
                    if bid is None:
                        continue
                    pm = QPixmapCache.find(f"cover_{bid}")
                    if pm and not pm.isNull():
                        item.setIcon(QIcon(pm))

    # ------------------------------------------------------------------
    # Feature: view mode (list / cards)
    # ------------------------------------------------------------------

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._view_list_btn.setChecked(mode == "list")
        self._view_card_btn.setChecked(mode == "cards")
        self._view_shelves_btn.setChecked(mode == "shelves")

        # Reset delete button — selection tracking is view-specific
        self._delete_sel_btn.setEnabled(False)
        self._delete_sel_btn.setText("Delete Selected")

        if mode == "list":
            self._stack.setCurrentIndex(0)
            # Re-evaluate table selection in case it was non-empty before switching
            self._on_table_selection_changed()
        elif mode == "cards":
            self._stack.setCurrentIndex(1)
            self._refresh_card_view()
        elif mode == "shelves":
            self._stack.setCurrentIndex(2)
            self._shelf_view.refresh_shelves()

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
    # Feature: settings dialog
    # ------------------------------------------------------------------

    def _open_settings(self, _section: str = "doc") -> None:
        from ui.dialogs.settings_dialog import SettingsDialog  # noqa: PLC0415
        dlg = SettingsDialog(parent=self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self) -> None:
        from ui.dialogs.settings_dialog import load_settings  # noqa: PLC0415
        cfg = load_settings()
        size  = cfg["doc_font_size"]
        khmer = cfg["khmer_font"]
        self._doc_delegate.update_settings(size, khmer)
        self._table.viewport().update()
        self._card_view.set_doc_font(size, khmer)
        if self._view_mode == "cards":
            self._card_view.viewport().update()

    def _build_menu(self) -> None:
        mb = self.menuBar()
        lib_menu = mb.addMenu("&Library")
        lib_menu.addAction("&Manage Categories…", self._open_category_manager)
        lib_menu.addSeparator()
        lib_menu.addAction("&Clear All Column Filters", self._clear_all_col_filters)
        lib_menu.addSeparator()
        lib_menu.addAction("Application Window Settings…",
                           lambda: self._open_settings("app"))
        lib_menu.addAction("Document Information Settings…",
                           lambda: self._open_settings("doc"))

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
        # Cancel any running cover loader before repopulating
        if self._cover_loader and self._cover_loader.isRunning():
            self._cover_loader.cancel()
            self._cover_loader.wait(500)

        self._table.setSortingEnabled(False)
        self._table.clearContents()
        self._table.setRowCount(len(books))
        self._table.verticalHeader().setDefaultSectionSize(64)

        self._book_id_to_row = {}
        for row_idx, book in enumerate(books):
            self._book_id_to_row[book.id] = row_idx
            for col_idx, value in enumerate(book.cell_values()):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col_idx == 0:
                    # Cover column: store book_id as UserRole; icon set later
                    item.setData(Qt.ItemDataRole.UserRole, book.id)
                self._table.setItem(row_idx, col_idx, item)

        self._table.setSortingEnabled(True)
        self._update_status()
        self._start_cover_loader(books)

    def _start_cover_loader(self, books: list[BookRow]) -> None:
        """Start a background thread to load cover thumbnails into QPixmapCache.

        Always runs regardless of cover-column visibility, so the card view
        always benefits from cached covers.
        """
        if self._cover_loader and self._cover_loader.isRunning():
            self._cover_loader.cancel()
            self._cover_loader.wait(500)

        # Count only books that actually have a cover file on disk
        self._covers_loaded = 0
        self._covers_total  = sum(
            1 for b in books
            if os.path.exists(os.path.join(self._covers_dir, f"{b.id}.jpg"))
        )

        # Batch card-view repaints: instead of one repaint per cover signal,
        # fire a single repaint every 150 ms so the UI stays responsive.
        self._cover_repaint_timer = QTimer(self)
        self._cover_repaint_timer.setInterval(150)
        self._cover_repaint_timer.timeout.connect(
            lambda: self._card_view.viewport().update()
            if self._view_mode == "cards" else None
        )
        if self._covers_total > 0:
            self._cover_repaint_timer.start()
            self._status_label.setText(
                f"Loading covers: 0 / {self._covers_total}"
            )

        self._cover_loader = _CoverLoader(books, self._covers_dir, parent=self)
        self._cover_loader.cover_loaded.connect(self._on_cover_loaded)
        self._cover_loader.finished.connect(self._on_covers_finished)
        self._cover_loader.start()

    def _on_cover_loaded(self, book_id: int, img: QImage) -> None:
        """Slot: convert QImage → QPixmap, update table icon + QPixmapCache for cards."""
        pm        = QPixmap.fromImage(img)
        icon      = QIcon(pm)
        cover_key = f"cover_{book_id}"

        # Feed the QPixmapCache so the card delegate can find the cover
        QPixmapCache.insert(cover_key, pm)

        # Update table row icon via O(1) lookup (only if cover column is shown).
        # Falls back to a linear scan if the table was re-sorted since load.
        if self._col_visible[0]:
            row = self._book_id_to_row.get(book_id)
            item = self._table.item(row, 0) if row is not None else None
            if item and item.data(Qt.ItemDataRole.UserRole) != book_id:
                # Table was re-sorted; find the real row
                item = None
                for r in range(self._table.rowCount()):
                    it = self._table.item(r, 0)
                    if it and it.data(Qt.ItemDataRole.UserRole) == book_id:
                        item = it
                        self._book_id_to_row[book_id] = r   # update cache
                        break
            if item:
                item.setIcon(icon)

        # Update counter and status bar
        self._covers_loaded += 1
        self._status_label.setText(
            f"Loading covers: {self._covers_loaded} / {self._covers_total}"
        )

    def _on_covers_finished(self) -> None:
        """All covers loaded — stop the repaint timer and do one final update."""
        if hasattr(self, "_cover_repaint_timer"):
            self._cover_repaint_timer.stop()
        if self._view_mode == "cards":
            self._card_view.viewport().update()
        self._update_status()   # restore normal status text

    # ------------------------------------------------------------------
    # Per-column filter
    # ------------------------------------------------------------------

    def _on_filter_requested(self, col: int) -> None:
        """Open the column filter popup for *col*."""
        if col >= len(_ALL_COLUMNS) or not _ALL_COLUMNS[col].filterable:
            return

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
            _ALL_COLUMNS[col].header, all_vals, current_filter, parent=self
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
        if self._view_mode != "list":
            return
        n = len(self._selected_book_ids())
        self._delete_sel_btn.setEnabled(n > 0)
        self._delete_sel_btn.setText(
            f"Delete Selected ({n})" if n > 1 else "Delete Selected"
        )

    def _on_card_selection_changed(self) -> None:
        """Keep the Delete Selected button in sync with card view selection."""
        if self._view_mode != "cards":
            return
        n = len(self._selected_card_ids())
        self._delete_sel_btn.setEnabled(n > 0)
        self._delete_sel_btn.setText(
            f"Delete Selected ({n})" if n > 1 else "Delete Selected"
        )

    def _selected_card_ids(self) -> list[int]:
        """Return unique book IDs for all currently selected cards."""
        seen: set[int] = set()
        ids: list[int] = []
        for it in self._card_view.selectedItems():
            d = it.data(Qt.ItemDataRole.UserRole)
            if d is not None and hasattr(d, "book_id") and d.book_id not in seen:
                seen.add(d.book_id)
                ids.append(d.book_id)
        return ids

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
        n = len(ids)
        if n == 1:
            book_id = ids[0]
            menu.addAction("Edit Metadata…",
                           lambda: self._open_book_detail(book_id))
            menu.addAction("Rename File…",
                           lambda: self._on_rename_file(book_id))
            menu.addSeparator()

        self._build_batch_actions(menu, ids)
        menu.addSeparator()
        self._build_shelf_submenu(menu, ids)
        menu.addSeparator()

        del_label = (f"Delete {n} book(s) from DB + Disk…" if n > 1
                     else "Delete from DB + Disk…")
        del_action: QAction = menu.addAction(del_label)
        del_action.setData(ids)
        del_action.triggered.connect(lambda: self._on_delete_selected())
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_card_context_menu(self, book_ids: list[int], global_pos: QPoint) -> None:
        menu = QMenu(self)
        n = len(book_ids)
        if n == 1:
            bid = book_ids[0]
            menu.addAction("Edit Metadata…", lambda: self._open_book_detail(bid))
            menu.addAction("Rename File…",   lambda: self._on_rename_file(bid))
            menu.addSeparator()

        self._build_batch_actions(menu, book_ids)
        menu.addSeparator()
        self._build_shelf_submenu(menu, book_ids)
        menu.addSeparator()
        del_label = (f"Delete {n} book(s) from DB + Disk…"
                     if n > 1 else "Delete from DB + Disk…")
        menu.addAction(del_label, lambda: self._delete_books(book_ids))
        menu.exec(global_pos)

    # ------------------------------------------------------------------
    # Multi-select delete (DB + disk)
    # ------------------------------------------------------------------

    def _on_delete_selected(self) -> None:
        if self._view_mode == "cards":
            ids = self._selected_card_ids()
        else:
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
    # Batch actions (rename / set author / set category)
    # ------------------------------------------------------------------

    def _build_batch_actions(self, menu: QMenu, book_ids: list[int]) -> None:
        """Append bulk-edit actions to *menu* for the given selection."""
        n = len(book_ids)
        suffix = f" {n} book(s)" if n > 1 else ""
        menu.addAction(
            f"Rename{suffix}\u2026",
            lambda: self._batch_rename_books(book_ids),
        )
        menu.addAction(
            f"Set Author for{suffix}\u2026",
            lambda: self._batch_set_author(book_ids),
        )
        menu.addAction(
            f"Set Category for{suffix}\u2026",
            lambda: self._batch_set_category(book_ids),
        )

    def _batch_rename_books(self, book_ids: list[int]) -> None:
        """Open the batch rename dialog for the given book IDs."""
        from pathlib import Path  # noqa: PLC0415
        from ui.dialogs.batch_operations_dialog import BatchRenameDialog  # noqa: PLC0415

        # Fetch required metadata from DB
        conn         = get_conn(self._db_path)
        ph           = ",".join("?" * len(book_ids))
        rows         = conn.execute(
            f"SELECT id, filename, filepath, title, author, year "
            f"FROM books WHERE id IN ({ph})",
            book_ids,
        ).fetchall()
        books_data   = [dict(r) for r in rows]

        dlg = BatchRenameDialog(books_data, self._db_path, parent=self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted

        # Remove any books deleted inside the dialog from main-window state
        deleted_ids = dlg.get_deleted_ids()
        if deleted_ids:
            deleted_set = set(deleted_ids)
            self._all_books = [b for b in self._all_books if b.id not in deleted_set]
            for bid in deleted_ids:
                for table_row in range(self._table.rowCount()):
                    first = self._table.item(table_row, 0)
                    if first and first.data(Qt.ItemDataRole.UserRole) == bid:
                        self._table.removeRow(table_row)
                        break
            if self._view_mode == "cards":
                self._refresh_card_view()
            self._update_status()

        if not accepted:
            return

        renames = dlg.get_renames()   # [(book_id, new_filename), ...]
        if not renames:
            return

        errors: list[str] = []
        done:   int       = 0

        # Build a quick lookup for filepath by book_id
        fp_map = {r["id"]: (r["filepath"] or "") for r in rows}

        for bid, new_name in renames:
            old_filepath = fp_map.get(bid, "")
            old_path     = Path(old_filepath) if old_filepath else None
            if old_path is None or not old_path.exists():
                errors.append(f"File not found: {old_filepath or '(no path)'}")
                continue
            new_path = old_path.parent / new_name
            if new_path.exists() and new_path != old_path:
                errors.append(
                    f'"{new_name}" already exists — skipped'
                )
                continue
            try:
                old_path.rename(new_path)
                conn = get_conn(self._db_path)
                conn.execute(
                    "UPDATE books SET filename=?, filepath=? WHERE id=?",
                    (new_name, str(new_path).replace("\\", "/"), bid),
                )
                conn.commit()
                self._refresh_book_row(bid)
                done += 1
            except PermissionError:
                errors.append(f'"{old_path.name}" is open — skipped')
            except Exception as exc:
                errors.append(f'"{old_path.name}": {exc}')

        self._status_label.setText(f"Renamed {done} file(s).")
        if errors:
            QMessageBox.warning(
                self, "Batch Rename — Partial Failure",
                "Some files could not be renamed:\n\n" + "\n".join(errors),
            )

    def _batch_set_author(self, book_ids: list[int]) -> None:
        """Prompt for an author name and apply it to all selected books."""
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415
        n    = len(book_ids)
        text, ok = QInputDialog.getText(
            self,
            "Set Author",
            f"Author name to apply to {n} selected book(s):",
        )
        if not ok:
            return
        author = text.strip()
        try:
            conn = get_conn(self._db_path)
            ph   = ",".join("?" * n)
            conn.execute(
                f"UPDATE books SET author=? WHERE id IN ({ph})",
                [author, *book_ids],
            )
            conn.commit()
            for bid in book_ids:
                self._refresh_book_row(bid)
            self._status_label.setText(
                f"Author set to \"{author}\" for {n} book(s)."
            )
            if self._view_mode == "cards":
                self._refresh_card_view()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _batch_set_category(self, book_ids: list[int]) -> None:
        """Open the category picker and apply the chosen category to all selected books."""
        from ui.dialogs.batch_operations_dialog import BatchSetCategoryDialog  # noqa: PLC0415
        n   = len(book_ids)
        dlg = BatchSetCategoryDialog(n, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        category, subcategory = dlg.get_category()
        if not category and not subcategory:
            return

        try:
            conn  = get_conn(self._db_path)
            ph    = ",".join("?" * n)
            parts = []
            args: list = []
            if category:
                parts.append("category=?")
                args.append(category)
            if subcategory:
                parts.append("subcategory=?")
                args.append(subcategory)
            args.extend(book_ids)
            conn.execute(
                f"UPDATE books SET {', '.join(parts)} WHERE id IN ({ph})",
                args,
            )
            conn.commit()
            for bid in book_ids:
                self._refresh_book_row(bid)
            msg = f"Category set to \"{category}\""
            if subcategory:
                msg += f" / \"{subcategory}\""
            self._status_label.setText(f"{msg} for {n} book(s).")
            if self._view_mode == "cards":
                self._refresh_card_view()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ------------------------------------------------------------------
    # Shelf helpers
    # ------------------------------------------------------------------

    def _build_shelf_submenu(self, parent_menu: QMenu, book_ids: list[int]) -> None:
        """Append an 'Add to Shelf ▶' submenu to *parent_menu*."""
        sub = parent_menu.addMenu("Add to Shelf \u25b6")
        # 'New Shelf…' always at the top
        new_action = sub.addAction("New Shelf\u2026")
        new_action.triggered.connect(lambda: self._create_shelf_and_add(book_ids))
        try:
            conn = get_conn(self._db_path)
            shelves = conn.execute(
                "SELECT id, name FROM shelves ORDER BY name COLLATE NOCASE"
            ).fetchall()
        except Exception:
            shelves = []
        if shelves:
            sub.addSeparator()
            for shelf in shelves:
                sid, sname = shelf["id"], shelf["name"]
                action = sub.addAction(sname)
                action.triggered.connect(
                    lambda checked, s=sid: self._add_books_to_shelf(s, book_ids)
                )

    def _add_books_to_shelf(self, shelf_id: int, book_ids: list[int]) -> None:
        """Add one or more books to a shelf (silently ignores duplicates)."""
        try:
            conn = get_conn(self._db_path)
            for bid in book_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO shelf_books (shelf_id, book_id) VALUES (?, ?)",
                    (shelf_id, bid),
                )
            conn.commit()
            n = len(book_ids)
            self._status_label.setText(
                f"Added {n} book{'s' if n > 1 else ''} to shelf."
            )
            if self._view_mode == "shelves":
                self._shelf_view.refresh_shelves()
        except Exception as exc:
            log.exception("Failed to add books to shelf")
            QMessageBox.critical(self, "Shelf Error", str(exc))

    def _create_shelf_and_add(self, book_ids: list[int]) -> None:
        """Prompt for a new shelf name, create it, and add books to it."""
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415
        name, ok = QInputDialog.getText(self, "New Shelf", "Shelf name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            conn = get_conn(self._db_path)
            conn.execute("INSERT INTO shelves (name) VALUES (?)", (name,))
            conn.commit()
            row = conn.execute(
                "SELECT id FROM shelves WHERE name=?", (name,)
            ).fetchone()
            if row:
                self._add_books_to_shelf(row["id"], book_ids)
        except Exception as exc:
            QMessageBox.critical(self, "Shelf Error", str(exc))

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
            dlg = BookDetailDialog(book_id, self._db_path, parent=self)
            # Refresh the row each time the user saves (dialog stays open)
            dlg.book_saved.connect(self._refresh_book_row)
            result = dlg.exec()
            if result == BookDetailDialog.DELETED:
                self._remove_book_row(book_id)
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
                """SELECT id, filename, title, author, year, language,
                          category, subcategory, difficulty, status,
                          reading_status, file_type, file_size, tags,
                          confidence_score, extraction_method, added_at, processed_at
                   FROM books WHERE id=?""",
                (book_id,),
            ).fetchone()
            if not r:
                return

            def _fmt_size(val) -> str:
                if not val:
                    return ""
                try:
                    return f"{int(val) / 1_048_576:.1f} MB"
                except (TypeError, ValueError):
                    return str(val)

            updated = BookRow(
                id                = r["id"],
                filename          = r["filename"]          or "",
                title             = r["title"]             or "",
                author            = r["author"]            or "",
                year              = str(r["year"]) if r["year"] else "",
                language          = r["language"]          or "",
                category          = r["category"]          or "",
                subcategory       = r["subcategory"]       or "",
                difficulty        = r["difficulty"]        or "",
                status            = r["status"]            or "",
                reading_status    = r["reading_status"]    or "",
                file_type         = r["file_type"]         or "",
                file_size         = _fmt_size(r["file_size"]),
                tags              = r["tags"]              or "",
                confidence_score  = (f"{r['confidence_score']:.0%}"
                                      if r["confidence_score"] else ""),
                extraction_method = r["extraction_method"] or "",
                added_at          = (r["added_at"]    or "")[:10],
                processed_at      = (r["processed_at"] or "")[:10],
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

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """Double-click the table corner button to reset all row heights."""
        if (
            isinstance(obj, QAbstractButton)
            and event.type() == QEvent.Type.MouseButtonDblClick
        ):
            vh = self._table.verticalHeader()
            for i in range(self._table.rowCount()):
                vh.resizeSection(i, vh.defaultSectionSize())
            return True
        return super().eventFilter(obj, event)

    def _restore_window_state(self) -> None:
        """Restore geometry and view mode from previous session (if enabled)."""
        if self._settings.value("app/remember_geometry", True, type=bool):
            geom = self._settings.value("app/geometry")
            if geom:
                self.restoreGeometry(geom)

        if self._settings.value("app/remember_view", True, type=bool):
            saved_view = self._settings.value("app/last_view", "", type=str)
            if saved_view in ("list", "cards", "shelves"):
                self._set_view(saved_view)
        else:
            default_view = self._settings.value("app/default_view", "list", type=str)
            if default_view in ("list", "cards", "shelves"):
                self._set_view(default_view)

    def closeEvent(self, event) -> None:  # noqa: N802
        # Save session state
        if self._settings.value("app/remember_geometry", True, type=bool):
            self._settings.setValue("app/geometry", self.saveGeometry())
        if self._settings.value("app/remember_view", True, type=bool):
            self._settings.setValue("app/last_view", self._view_mode)

        if self._loader and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(2_000)
        if self._cover_loader and self._cover_loader.isRunning():
            self._cover_loader.cancel()
            self._cover_loader.wait(1_000)
        super().closeEvent(event)
