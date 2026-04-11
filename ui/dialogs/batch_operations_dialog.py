"""
ui/dialogs/batch_operations_dialog.py — Batch operations on multiple selected books.

Provides:
  BatchRenameDialog    — rename N files using a template, with a preview table.
  BatchSetFieldDialog  — set a single text field (e.g. Author) on N books.
  BatchSetCategoryDialog — pick category + subcategory from FIXED_TAXONOMY and
                           apply to N books.
"""

import os
import re
import unicodedata

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Helpers shared with rename_file_dialog
# ---------------------------------------------------------------------------

_STRIP_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def _sanitise(raw: str) -> str:
    normalised = unicodedata.normalize("NFKC", raw or "")
    cleaned    = _STRIP_RE.sub("", normalised)
    cleaned    = re.sub(r"[\s\-.]+" , " ", cleaned).strip().lower()
    cleaned    = re.sub(r"\s+", "_", cleaned)
    return re.sub(r"_+", "_", cleaned).strip("_")


def _make_name(book: dict, template: str) -> str:
    """Build a sanitised filename from *book* dict according to *template*.

    Template tokens: {title}, {author}, {year}  — missing fields are omitted.
    The original extension is always preserved.
    """
    ext   = os.path.splitext(book.get("filename") or "")[1]
    parts = {
        "title":  _sanitise(book.get("title")  or ""),
        "author": _sanitise(book.get("author") or ""),
        "year":   _sanitise(str(book.get("year") or "")),
    }
    # Walk the template tokens and keep only non-empty ones
    # e.g. "{title}_by_{author}_{year}" → "ml_by_ng_2023"
    result = template
    for key, val in parts.items():
        if val:
            result = result.replace(f"{{{key}}}", val)
        else:
            # Remove the token; also eat one adjacent underscore to avoid doubles
            result = re.sub(rf"_?\{{{key}\}}_?", "_", result)

    result = re.sub(r"_+", "_", result).strip("_")
    return (result or "unnamed") + ext


# Predefined template choices shown in the combo
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
    """Preview + confirm renaming for multiple files.

    Parameters
    ----------
    books:
        List of dicts: ``{id, filename, filepath, title, author, year}``
    """

    def __init__(self, books: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._books = books
        self.setWindowTitle(f"Batch Rename — {len(books)} file(s)")
        self.setMinimumWidth(760)
        self.resize(900, 520)
        self.setModal(True)
        self._build_ui()
        self._generate_names()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(8)

        # ── Template row ──────────────────────────────────────────────
        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel("Name template:"))
        self._tpl_combo = QComboBox()
        for label, _ in _TEMPLATES:
            self._tpl_combo.addItem(label)
        self._tpl_combo.currentIndexChanged.connect(self._generate_names)
        tpl_row.addWidget(self._tpl_combo, 1)
        tpl_row.addWidget(QLabel("or custom:"))
        self._tpl_edit = QLineEdit()
        self._tpl_edit.setPlaceholderText("{title}_by_{author}_{year}")
        self._tpl_edit.textEdited.connect(self._on_custom_template)
        tpl_row.addWidget(self._tpl_edit, 1)
        outer.addLayout(tpl_row)

        outer.addWidget(QLabel(
            "<small>Tokens: <tt>{title}</tt>, <tt>{author}</tt>, <tt>{year}</tt> "
            "— missing metadata is silently omitted.  Original extension is always kept.</small>"
        ))

        # ── Preview table ─────────────────────────────────────────────
        self._table = QTableWidget(len(self._books), 3)
        self._table.setHorizontalHeaderLabels(["", "Current filename", "New filename"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self._table.setColumnWidth(0, 28)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked |
            QTableWidget.EditTrigger.SelectedClicked
        )
        self._table.cellChanged.connect(self._on_cell_changed)
        outer.addWidget(self._table)

        # ── Footer info + buttons ─────────────────────────────────────
        self._info_label = QLabel()
        outer.addWidget(self._info_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
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
        if custom:
            return custom
        return _TEMPLATES[self._tpl_combo.currentIndex()][1]

    def _generate_names(self) -> None:
        tpl = self._current_template()
        self._table.blockSignals(True)
        for row, book in enumerate(self._books):
            # Column 0: checkbox
            cb = self._table.cellWidget(row, 0)
            if cb is None:
                cb_widget = QWidget()
                cb_layout = QHBoxLayout(cb_widget)
                cb_layout.setContentsMargins(4, 0, 4, 0)
                cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                chk = QCheckBox()
                chk.setChecked(True)
                chk.stateChanged.connect(self._update_footer)
                cb_layout.addWidget(chk)
                self._table.setCellWidget(row, 0, cb_widget)

            # Column 1: current filename (read-only)
            cur_item = QTableWidgetItem(book.get("filename") or "")
            cur_item.setFlags(cur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            cur_item.setForeground(Qt.GlobalColor.darkGray)
            self._table.setItem(row, 1, cur_item)

            # Column 2: proposed name (editable)
            proposed = _make_name(book, tpl)
            new_item = QTableWidgetItem(proposed)
            # Highlight if name unchanged
            if proposed == (book.get("filename") or ""):
                new_item.setForeground(Qt.GlobalColor.darkGray)
            self._table.setItem(row, 2, new_item)

        self._table.blockSignals(False)
        self._update_footer()

    def _on_custom_template(self, _text: str) -> None:
        self._generate_names()

    def _on_cell_changed(self, row: int, col: int) -> None:
        if col == 2:
            self._update_footer()

    def _update_footer(self) -> None:
        checked = self._checked_count()
        conflicts = self._conflict_count()
        self._info_label.setText(
            f"<small>{checked} of {len(self._books)} file(s) selected for rename"
            + (f"  —  <b style='color:#c0392b'>{conflicts} duplicate name(s)</b>"
               if conflicts else "")
            + "</small>"
        )
        self._apply_btn.setEnabled(checked > 0 and conflicts == 0)
        self._apply_btn.setText(f"Rename ({checked})" if checked else "Rename")

    def _checked_count(self) -> int:
        count = 0
        for row in range(self._table.rowCount()):
            if self._row_checked(row):
                count += 1
        return count

    def _conflict_count(self) -> int:
        names: list[str] = []
        for row in range(self._table.rowCount()):
            if self._row_checked(row):
                item = self._table.item(row, 2)
                if item:
                    names.append(item.text().strip().lower())
        return len(names) - len(set(names))

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
        """Return [(book_id, new_filename), ...] for all checked rows."""
        result = []
        for row, book in enumerate(self._books):
            if not self._row_checked(row):
                continue
            item = self._table.item(row, 2)
            if item:
                new_name = item.text().strip()
                if new_name and new_name != (book.get("filename") or ""):
                    result.append((book["id"], new_name))
        return result


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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        outer.addWidget(QLabel(
            f"<b>Apply to {self._n} selected book(s)</b>"
        ))

        # Category combo
        from PyQt6.QtWidgets import QFormLayout  # noqa: PLC0415
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
        """Return (category, subcategory); empty string means 'keep existing'."""
        return (
            self._cat_combo.currentData() or "",
            self._sub_combo.currentData() or "",
        )
