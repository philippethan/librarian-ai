"""
ui/dialogs/batch_operations_dialog.py — Batch operations on multiple selected books.

Provides:
  BatchRenameDialog      — rename N files with a template; sortable/filterable
                           preview table; right-click to delete or uncheck a row.
  BatchSetCategoryDialog — pick category + subcategory from FIXED_TAXONOMY and
                           apply to N books.
"""

import logging
import os
import re
import unicodedata

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from ui.theme import COLORS as _T
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STRIP_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def _sanitise(raw: str) -> str:
    normalised = unicodedata.normalize("NFKC", raw or "")
    cleaned    = _STRIP_RE.sub("", normalised)
    cleaned    = re.sub(r"[\s\-.]+" , " ", cleaned).strip()
    cleaned    = re.sub(r"\s+", "_", cleaned)
    return re.sub(r"_+", "_", cleaned).strip("_")


def _make_name(book: dict, template: str) -> str:
    """Build a sanitised filename from *book* dict according to *template*.

    Tokens: ``{title}``, ``{author}``, ``{year}`` — missing fields are omitted.
    The original file extension is always preserved.
    """
    ext   = os.path.splitext(book.get("filename") or "")[1]
    parts = {
        "title":  _sanitise(book.get("title")  or ""),
        "author": _sanitise(book.get("author") or ""),
        "year":   _sanitise(str(book.get("year") or "")),
    }
    result = template
    for key, val in parts.items():
        if val:
            result = result.replace(f"{{{key}}}", val)
        else:
            result = re.sub(rf"_?\{{{key}\}}_?", "_", result)

    result = re.sub(r"_+", "_", result).strip("_")
    return (result or "unnamed") + ext


# Predefined templates shown in the combo
_TEMPLATES: list[tuple[str, str]] = [
    ("title_author_year  (recommended)", "{title}_{author}_{year}"),
    ("author_title_year",                "{author}_{title}_{year}"),
    ("title_year",                       "{title}_{year}"),
    ("title_author",                     "{title}_{author}"),
    ("title only",                       "{title}"),
]


# ---------------------------------------------------------------------------
# BatchRenameDialog
# ---------------------------------------------------------------------------

class BatchRenameDialog(QDialog):
    """Preview and confirm renaming for multiple book files.

    Features
    --------
    * Sortable columns — click any header to sort ascending/descending.
    * Filter bar — instantly hides rows whose filenames don't match the text.
    * Inline editing — double-click any cell in the "New filename" column.
    * Conflict highlighting — duplicate proposed names turn red; the table
      auto-scrolls to the first conflict and the Rename button stays disabled.
    * Right-click context menu:
        - Uncheck / Check (skip or include the row in the rename batch)
        - Delete from DB and Disk — removes the file permanently, live.

    Parameters
    ----------
    books:
        List of dicts: ``{id, filename, filepath, title, author, year}``.
    db_path:
        SQLite database path; needed for in-dialog deletion.
    """

    def __init__(self, books: list[dict], db_path: str, parent=None) -> None:
        super().__init__(parent)
        self._db_path     = db_path
        self._books       = list(books)
        self._books_by_id = {b["id"]: b for b in books}
        self._deleted_ids: list[int] = []

        self.setWindowTitle(f"Batch Rename — {len(books)} file(s)")
        self.setMinimumWidth(820)
        self.resize(1000, 580)
        self.setModal(True)
        self._build_ui()
        self._generate_names()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(6)

        # ── Template row ──────────────────────────────────────────────
        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel("Template:"))
        self._tpl_combo = QComboBox()
        for label, _ in _TEMPLATES:
            self._tpl_combo.addItem(label)
        self._tpl_combo.currentIndexChanged.connect(self._generate_names)
        tpl_row.addWidget(self._tpl_combo, 2)

        tpl_row.addWidget(QLabel("Custom:"))
        self._tpl_edit = QLineEdit()
        self._tpl_edit.setPlaceholderText("{title}_{author}_{year}")
        self._tpl_edit.textEdited.connect(self._generate_names)
        tpl_row.addWidget(self._tpl_edit, 2)
        outer.addLayout(tpl_row)

        outer.addWidget(QLabel(
            "<small>Tokens: <tt>{title}</tt>&nbsp; <tt>{author}</tt>&nbsp; "
            "<tt>{year}</tt> — missing fields are omitted. "
            "Extension is always preserved. "
            "Double-click a cell in the <i>New filename</i> column to edit it manually.</small>"
        ))

        # ── Filter bar ────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Type to filter by current or new filename…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_edit)
        outer.addLayout(filter_row)

        # ── Preview table ─────────────────────────────────────────────
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["", "Current filename", "New filename"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 32)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked |
            QTableWidget.EditTrigger.SelectedClicked
        )
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.cellChanged.connect(self._on_cell_changed)
        # Sorting is enabled after population to avoid per-insert overhead
        outer.addWidget(self._table)

        # ── Footer ────────────────────────────────────────────────────
        self._info_label = QLabel()
        outer.addWidget(self._info_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Close")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._apply_btn = QPushButton("Rename")
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._apply_btn)
        outer.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Name generation
    # ------------------------------------------------------------------

    def _current_template(self) -> str:
        custom = self._tpl_edit.text().strip()
        return custom if custom else _TEMPLATES[self._tpl_combo.currentIndex()][1]

    def _generate_names(self) -> None:
        """(Re)populate the table from self._books using the current template."""
        tpl = self._current_template()

        self._table.setSortingEnabled(False)
        self._table.blockSignals(True)
        self._table.clearContents()
        self._table.setRowCount(len(self._books))

        for row, book in enumerate(self._books):
            # Col 0 — centred checkbox widget
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.setContentsMargins(4, 0, 4, 0)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(True)
            chk.stateChanged.connect(self._update_footer)
            cb_layout.addWidget(chk)
            self._table.setCellWidget(row, 0, cb_widget)

            # Col 1 — current filename, read-only; UserRole carries book_id
            cur_item = QTableWidgetItem(book.get("filename") or "")
            cur_item.setFlags(cur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            cur_item.setData(Qt.ItemDataRole.UserRole, book["id"])
            cur_item.setForeground(Qt.GlobalColor.darkGray)
            self._table.setItem(row, 1, cur_item)

            # Col 2 — proposed name, editable
            proposed = _make_name(book, tpl)
            new_item = QTableWidgetItem(proposed)
            self._table.setItem(row, 2, new_item)

        self._table.blockSignals(False)
        self._table.setSortingEnabled(True)
        self._apply_filter()

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _apply_filter(self) -> None:
        """Show/hide rows based on the filter bar text."""
        needle = self._filter_edit.text().strip().lower()
        for row in range(self._table.rowCount()):
            cur = (self._table.item(row, 1) or QTableWidgetItem()).text().lower()
            new = (self._table.item(row, 2) or QTableWidgetItem()).text().lower()
            hidden = bool(needle) and needle not in cur and needle not in new
            self._table.setRowHidden(row, hidden)
        self._update_footer()

    # ------------------------------------------------------------------
    # Conflict colouring + footer
    # ------------------------------------------------------------------

    def _on_cell_changed(self, row: int, col: int) -> None:
        if col == 2:
            self._update_footer()

    def _update_footer(self) -> None:
        """Colour-code column 2 cells and refresh the footer + Apply button."""
        # Count proposed names across visible + checked rows only
        name_count: dict[str, int] = {}
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row) or not self._row_checked(row):
                continue
            item = self._table.item(row, 2)
            if item:
                key = item.text().strip().lower()
                name_count[key] = name_count.get(key, 0) + 1

        duplicates          = {k for k, v in name_count.items() if v > 1}
        first_conflict_row: int | None = None

        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            cur_item = self._table.item(row, 1)
            new_item = self._table.item(row, 2)
            if cur_item is None or new_item is None:
                continue

            book_id   = cur_item.data(Qt.ItemDataRole.UserRole)
            book      = self._books_by_id.get(book_id, {})
            name      = new_item.text().strip()
            checked   = self._row_checked(row)

            if checked and name.lower() in duplicates:
                new_item.setBackground(QColor(_T["danger_bg"]))
                new_item.setForeground(QColor(_T["danger"]))
                if first_conflict_row is None:
                    first_conflict_row = row
            elif name == (book.get("filename") or ""):
                new_item.setBackground(QColor(0, 0, 0, 0))
                new_item.setForeground(QColor(_T["text_dim"]))
            else:
                new_item.setBackground(QColor(0, 0, 0, 0))
                new_item.setForeground(QColor(_T["text_primary"]))
        self._table.blockSignals(False)

        if first_conflict_row is not None:
            self._table.scrollToItem(
                self._table.item(first_conflict_row, 2),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

        total   = self._table.rowCount()
        visible = sum(1 for r in range(total) if not self._table.isRowHidden(r))
        checked = sum(
            1 for r in range(total)
            if not self._table.isRowHidden(r) and self._row_checked(r)
        )
        n_dup = len(duplicates)

        if n_dup:
            hint = (
                f"  \u2014  <b style='color:#c0392b'>"
                f"{n_dup} duplicate name(s) highlighted in red."
                f"</b> Edit the cells or uncheck/delete those rows."
            )
        else:
            hint = ""

        filter_note = f"  (filtered: {visible} of {total})" if visible != total else ""
        self._info_label.setText(
            f"<small>{checked} selected for rename{filter_note}{hint}</small>"
        )
        self._apply_btn.setEnabled(checked > 0 and n_dup == 0)
        self._apply_btn.setText(f"Rename ({checked})" if checked else "Rename")

    # ------------------------------------------------------------------
    # Context menu — right-click on a row
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or self._table.isRowHidden(row):
            return

        cur_item = self._table.item(row, 1)
        if cur_item is None:
            return
        book_id  = cur_item.data(Qt.ItemDataRole.UserRole)
        book     = self._books_by_id.get(book_id, {})
        filename = book.get("filename") or f"id={book_id}"

        menu = QMenu(self)

        # Toggle check state
        is_checked = self._row_checked(row)
        chk_label  = "Uncheck (skip rename)" if is_checked else "Check (include in rename)"
        menu.addAction(chk_label, lambda: self._toggle_row_check(row))

        menu.addSeparator()

        # Open containing folder (convenience)
        open_action = menu.addAction("Show in Explorer")
        filepath = book.get("filepath") or ""
        open_action.setEnabled(bool(filepath))
        open_action.triggered.connect(lambda: self._show_in_explorer(filepath))

        menu.addSeparator()

        del_action = menu.addAction(f'Delete "{filename}" from DB and Disk\u2026')
        del_action.setToolTip("Permanently remove this file and its database record")
        del_action_style = "color: #c0392b;"
        del_action.triggered.connect(lambda: self._delete_book_row(row))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _toggle_row_check(self, row: int) -> None:
        widget = self._table.cellWidget(row, 0)
        if widget:
            chk = widget.findChild(QCheckBox)
            if chk:
                chk.setChecked(not chk.isChecked())

    def _show_in_explorer(self, filepath: str) -> None:
        if filepath:
            norm = os.path.normpath(filepath)
            try:
                import subprocess  # noqa: S404
                subprocess.Popen(["explorer", "/select,", norm])
            except Exception as exc:
                log.warning("Could not open Explorer: %s", exc)

    def _delete_book_row(self, row: int) -> None:
        """Delete the book at *row* from disk and DB, then remove the row."""
        cur_item = self._table.item(row, 1)
        if cur_item is None:
            return
        book_id  = cur_item.data(Qt.ItemDataRole.UserRole)
        book     = self._books_by_id.get(book_id, {})
        filename = book.get("filename") or f"id={book_id}"
        filepath = book.get("filepath") or ""

        reply = QMessageBox.question(
            self,
            "Delete File",
            f'Permanently delete "{filename}" from the database and disk?\n\n'
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Delete from disk
        if filepath:
            norm = os.path.normpath(filepath)
            if os.path.exists(norm):
                try:
                    os.remove(norm)
                    log.info("Deleted file: %s", norm)
                except PermissionError:
                    QMessageBox.warning(
                        self, "Delete Failed",
                        f'"{filename}" is open in another application.',
                    )
                    return
                except Exception as exc:
                    QMessageBox.critical(self, "Delete Failed", str(exc))
                    return

        # Delete from DB
        try:
            from backend.db import get_conn  # noqa: PLC0415
            conn = get_conn(self._db_path)
            conn.execute("DELETE FROM books WHERE id=?", (book_id,))
            conn.commit()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))
            return

        # Remove from table and internal tracking
        self._table.removeRow(row)
        self._deleted_ids.append(book_id)
        self._books_by_id.pop(book_id, None)
        self._update_footer()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_checked(self, row: int) -> bool:
        widget = self._table.cellWidget(row, 0)
        if widget is None:
            return False
        chk = widget.findChild(QCheckBox)
        return chk is not None and chk.isChecked()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_renames(self) -> list[tuple[int, str]]:
        """Return ``[(book_id, new_filename), ...]`` for all checked, visible rows."""
        result = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row) or not self._row_checked(row):
                continue
            cur_item = self._table.item(row, 1)
            new_item = self._table.item(row, 2)
            if cur_item is None or new_item is None:
                continue
            book_id  = cur_item.data(Qt.ItemDataRole.UserRole)
            book     = self._books_by_id.get(book_id, {})
            new_name = new_item.text().strip()
            if new_name and new_name != (book.get("filename") or ""):
                result.append((book_id, new_name))
        return result

    def get_deleted_ids(self) -> list[int]:
        """Return book IDs that were deleted while the dialog was open."""
        return list(self._deleted_ids)


# ---------------------------------------------------------------------------
# BatchSetCategoryDialog
# ---------------------------------------------------------------------------

class BatchSetCategoryDialog(QDialog):
    """Pick a category and subcategory to apply to N books."""

    def __init__(self, n_books: int, parent=None) -> None:
        super().__init__(parent)
        self._n = n_books
        self.setWindowTitle(f"Set Category — {n_books} book(s)")
        self.setMinimumWidth(380)
        self.setModal(True)
        self._taxonomy: dict[str, list[str]] = {}
        self._load_taxonomy()
        self._build_ui()

    def _load_taxonomy(self) -> None:
        try:
            from backend.app import FIXED_TAXONOMY  # noqa: PLC0415
            self._taxonomy = dict(FIXED_TAXONOMY)
        except Exception:
            self._taxonomy = {}

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QFormLayout  # noqa: PLC0415
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        outer.addWidget(QLabel(f"<b>Apply to {self._n} selected book(s)</b>"))

        form = QFormLayout()
        form.setSpacing(8)

        self._cat_combo = QComboBox()
        self._cat_combo.addItem("(keep existing)", "")
        for cat in sorted(self._taxonomy.keys()):
            self._cat_combo.addItem(cat, cat)
        self._cat_combo.currentIndexChanged.connect(self._on_cat_changed)
        form.addRow("Category:", self._cat_combo)

        self._sub_combo = QComboBox()
        self._sub_combo.addItem("(keep existing)", "")
        form.addRow("Subcategory:", self._sub_combo)

        outer.addLayout(form)
        outer.addWidget(QLabel(
            "<small>Choose '(keep existing)' for any field you don't want to change.</small>"
        ))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        apply_btn = QPushButton(f"Apply to {self._n}")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self.accept)
        btn_row.addWidget(apply_btn)
        outer.addLayout(btn_row)

    def _on_cat_changed(self, _index: int) -> None:
        cat  = self._cat_combo.currentData()
        subs = self._taxonomy.get(cat, [])
        self._sub_combo.clear()
        self._sub_combo.addItem("(keep existing)", "")
        for s in subs:
            self._sub_combo.addItem(s, s)

    def get_category(self) -> tuple[str, str]:
        """Return ``(category, subcategory)``; empty string means keep existing."""
        return (
            self._cat_combo.currentData() or "",
            self._sub_combo.currentData() or "",
        )
