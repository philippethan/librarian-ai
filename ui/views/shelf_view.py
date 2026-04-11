"""
ui/views/shelf_view.py — Shelf management view.

Provides a two-panel widget:
  Left  — list of all shelves with New / Rename / Delete controls.
  Right — books contained in the selected shelf, with Open and Remove actions.

Emits book_activated(book_id) when the user wants to view a book's details.
All DB access is done synchronously (this widget lives on the main thread).
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from backend.db import get_conn

log = logging.getLogger(__name__)


class ShelfView(QWidget):
    """
    Two-panel shelf browser.

    Left panel:  list of shelves (click to load books on the right).
    Right panel: books on the selected shelf.
    """

    book_activated = pyqtSignal(int)   # book_id — open BookDetailDialog

    def __init__(self, db_path: str, covers_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._db_path    = db_path
        self._covers_dir = covers_dir
        self._current_shelf_id: int | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left panel — shelf list ────────────────────────────────────
        left = QWidget()
        lv   = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        lv.setSpacing(4)

        lv.addWidget(QLabel("<b>Shelves</b>"))

        self._shelf_list = QListWidget()
        self._shelf_list.currentItemChanged.connect(self._on_shelf_selected)
        self._shelf_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._shelf_list.customContextMenuRequested.connect(self._on_shelf_context_menu)
        lv.addWidget(self._shelf_list)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("New")
        new_btn.setToolTip("Create a new shelf")
        new_btn.clicked.connect(self._on_new_shelf)
        btn_row.addWidget(new_btn)

        rename_btn = QPushButton("Rename")
        rename_btn.setToolTip("Rename the selected shelf")
        rename_btn.clicked.connect(self._on_rename_shelf)
        btn_row.addWidget(rename_btn)

        del_btn = QPushButton("Delete")
        del_btn.setToolTip("Delete the selected shelf (books are NOT deleted)")
        del_btn.setStyleSheet("color: #c0392b;")
        del_btn.clicked.connect(self._on_delete_shelf)
        btn_row.addWidget(del_btn)

        lv.addLayout(btn_row)
        splitter.addWidget(left)

        # ── Right panel — books on selected shelf ──────────────────────
        right = QWidget()
        rv    = QVBoxLayout(right)
        rv.setContentsMargins(4, 8, 8, 8)
        rv.setSpacing(4)

        self._shelf_title = QLabel("<i>Select a shelf to see its books</i>")
        rv.addWidget(self._shelf_title)

        self._book_list = QListWidget()
        self._book_list.setAlternatingRowColors(True)
        self._book_list.itemDoubleClicked.connect(self._on_book_double_clicked)
        self._book_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._book_list.customContextMenuRequested.connect(self._on_book_context_menu)
        rv.addWidget(self._book_list)

        book_btn_row = QHBoxLayout()
        open_btn = QPushButton("Open Book")
        open_btn.setToolTip("Open the selected book's detail dialog")
        open_btn.clicked.connect(self._on_open_book)
        book_btn_row.addWidget(open_btn)

        remove_btn = QPushButton("Remove from Shelf")
        remove_btn.setToolTip("Remove the selected book(s) from this shelf")
        remove_btn.setStyleSheet("color: #c0392b;")
        remove_btn.clicked.connect(self._on_remove_from_shelf)
        book_btn_row.addWidget(remove_btn)
        book_btn_row.addStretch()

        rv.addLayout(book_btn_row)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 660])

        outer.addWidget(splitter)

    # ------------------------------------------------------------------
    # Public API — called by MainWindow
    # ------------------------------------------------------------------

    def refresh_shelves(self) -> None:
        """Reload the shelf list from the DB."""
        current_id = self._current_shelf_id
        self._shelf_list.clear()
        try:
            conn = get_conn(self._db_path)
            rows = conn.execute(
                """SELECT s.id, s.name,
                          COUNT(sb.book_id) AS book_count
                   FROM shelves s
                   LEFT JOIN shelf_books sb ON s.id = sb.shelf_id
                   GROUP BY s.id
                   ORDER BY s.name COLLATE NOCASE"""
            ).fetchall()
        except Exception as exc:
            log.error("Failed to load shelves: %s", exc)
            return

        for row in rows:
            label = f"{row['name']}  ({row['book_count']})"
            item  = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self._shelf_list.addItem(item)

        # Re-select the previously selected shelf
        if current_id is not None:
            for i in range(self._shelf_list.count()):
                it = self._shelf_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == current_id:
                    self._shelf_list.setCurrentItem(it)
                    break

    # ------------------------------------------------------------------
    # Shelf selection
    # ------------------------------------------------------------------

    def _on_shelf_selected(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            self._current_shelf_id = None
            self._shelf_title.setText("<i>Select a shelf to see its books</i>")
            self._book_list.clear()
            return

        shelf_id   = current.data(Qt.ItemDataRole.UserRole)
        shelf_name = current.text().rsplit("  (", 1)[0]   # strip count suffix
        self._current_shelf_id = shelf_id
        self._shelf_title.setText(f"<b>{shelf_name}</b>")
        self._load_shelf_books(shelf_id)

    def _load_shelf_books(self, shelf_id: int) -> None:
        self._book_list.clear()
        try:
            conn = get_conn(self._db_path)
            rows = conn.execute(
                """SELECT b.id, b.title, b.author, b.year, b.status
                   FROM books b
                   JOIN shelf_books sb ON b.id = sb.book_id
                   WHERE sb.shelf_id = ?
                   ORDER BY COALESCE(b.title, b.filename) COLLATE NOCASE""",
                (shelf_id,),
            ).fetchall()
        except Exception as exc:
            log.error("Failed to load shelf books: %s", exc)
            return

        for row in rows:
            parts = []
            if row["title"]:
                parts.append(row["title"])
            if row["author"]:
                parts.append(f"— {row['author']}")
            if row["year"]:
                parts.append(f"({row['year']})")
            label = " ".join(parts) if parts else f"[id={row['id']}]"
            item  = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self._book_list.addItem(item)

    # ------------------------------------------------------------------
    # Shelf CRUD
    # ------------------------------------------------------------------

    def _on_new_shelf(self) -> None:
        name, ok = QInputDialog.getText(self, "New Shelf", "Shelf name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            conn = get_conn(self._db_path)
            conn.execute("INSERT INTO shelves (name) VALUES (?)", (name,))
            conn.commit()
            self.refresh_shelves()
            # Select the newly created shelf
            for i in range(self._shelf_list.count()):
                it = self._shelf_list.item(i)
                if it and it.text().startswith(name + "  ("):
                    self._shelf_list.setCurrentItem(it)
                    break
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not create shelf:\n{exc}")

    def _on_rename_shelf(self) -> None:
        item = self._shelf_list.currentItem()
        if not item:
            return
        shelf_id   = item.data(Qt.ItemDataRole.UserRole)
        old_name   = item.text().rsplit("  (", 1)[0]
        name, ok   = QInputDialog.getText(
            self, "Rename Shelf", "New name:", text=old_name
        )
        if not ok or not name.strip() or name.strip() == old_name:
            return
        try:
            conn = get_conn(self._db_path)
            conn.execute(
                "UPDATE shelves SET name=? WHERE id=?", (name.strip(), shelf_id)
            )
            conn.commit()
            self.refresh_shelves()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not rename shelf:\n{exc}")

    def _on_delete_shelf(self) -> None:
        item = self._shelf_list.currentItem()
        if not item:
            return
        shelf_id   = item.data(Qt.ItemDataRole.UserRole)
        shelf_name = item.text().rsplit("  (", 1)[0]
        reply = QMessageBox.question(
            self, "Delete Shelf",
            f'Delete shelf "{shelf_name}"?\n\nBooks in the shelf will NOT be deleted.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = get_conn(self._db_path)
            conn.execute("DELETE FROM shelves WHERE id=?", (shelf_id,))
            conn.commit()
            self._current_shelf_id = None
            self._book_list.clear()
            self._shelf_title.setText("<i>Select a shelf to see its books</i>")
            self.refresh_shelves()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not delete shelf:\n{exc}")

    def _on_shelf_context_menu(self, pos) -> None:
        item = self._shelf_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("Rename", self._on_rename_shelf)
        menu.addAction("Delete", self._on_delete_shelf)
        menu.exec(self._shelf_list.mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Book actions
    # ------------------------------------------------------------------

    def _on_book_double_clicked(self, item: QListWidgetItem) -> None:
        book_id = item.data(Qt.ItemDataRole.UserRole)
        if book_id is not None:
            self.book_activated.emit(book_id)

    def _on_open_book(self) -> None:
        item = self._book_list.currentItem()
        if item:
            book_id = item.data(Qt.ItemDataRole.UserRole)
            if book_id is not None:
                self.book_activated.emit(book_id)

    def _on_remove_from_shelf(self) -> None:
        if self._current_shelf_id is None:
            return
        selected = self._book_list.selectedItems()
        if not selected:
            return
        book_ids = [
            it.data(Qt.ItemDataRole.UserRole)
            for it in selected
            if it.data(Qt.ItemDataRole.UserRole) is not None
        ]
        if not book_ids:
            return
        try:
            conn  = get_conn(self._db_path)
            ph    = ",".join("?" * len(book_ids))
            conn.execute(
                f"DELETE FROM shelf_books WHERE shelf_id=? AND book_id IN ({ph})",
                [self._current_shelf_id, *book_ids],
            )
            conn.commit()
            self._load_shelf_books(self._current_shelf_id)
            self.refresh_shelves()   # update book count in left panel
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not remove from shelf:\n{exc}")

    def _on_book_context_menu(self, pos) -> None:
        item = self._book_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("Open Book", self._on_open_book)
        menu.addSeparator()
        menu.addAction("Remove from Shelf", self._on_remove_from_shelf)
        menu.exec(self._book_list.mapToGlobal(pos))
