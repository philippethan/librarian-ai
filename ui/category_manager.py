"""
ui/category_manager.py — CRUD dialog for the canonical_categories table.

Schema (from backend/app.py):
    canonical_categories(id PK, category TEXT, subcategory TEXT DEFAULT '')
    UNIQUE (category, subcategory)

Two-level model:
    top-level  row: subcategory = ''  (one per category name)
    child      row: subcategory = <name>

On first open, if the table is empty, every entry from FIXED_TAXONOMY is
seeded so the user sees the full built-in taxonomy immediately.

Edit and Delete propagate to the denormalised books.category / subcategory
columns so the library stays consistent.

Static helper used by other dialogs:
    CategoryManagerDialog.load_taxonomy(db_path) -> dict[str, list[str]]
"""

import logging
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from backend.db import get_conn

log = logging.getLogger(__name__)

DB_PATH: str = os.environ.get("DB_PATH", "backend/data/librarian.db")

try:
    from backend.app import FIXED_TAXONOMY as _SEED_TAXONOMY  # noqa: PLC0415
except Exception:
    log.warning("Could not import FIXED_TAXONOMY — seeding will be skipped")
    _SEED_TAXONOMY: dict[str, list[str]] = {}

# UserRole payload keys stored on every QTreeWidgetItem
_ROLE = Qt.ItemDataRole.UserRole


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class CategoryManagerDialog(QDialog):
    """Manage the canonical_categories taxonomy table."""

    categories_updated = pyqtSignal()  # emitted after any Add / Edit / Delete

    def __init__(self, db_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._db_path = db_path or DB_PATH

        self.setWindowTitle("Category Manager")
        self.setMinimumSize(460, 480)
        self.resize(520, 580)
        self.setModal(True)

        self._build_ui()
        self._load_or_seed()

    # ------------------------------------------------------------------
    # Static helper — lets BookDetailDialog (and others) get the taxonomy
    # without instantiating the dialog
    # ------------------------------------------------------------------

    @staticmethod
    def load_taxonomy(db_path: str | None = None) -> dict[str, list[str]]:
        """
        Return the full taxonomy as {category: [subcategory, ...]} from
        canonical_categories.  Falls back to FIXED_TAXONOMY if the table
        is empty or unreadable.
        """
        path = db_path or os.environ.get("DB_PATH", "backend/data/librarian.db")
        try:
            conn = get_conn(path)
            rows = conn.execute(
                "SELECT category, subcategory "
                "FROM canonical_categories "
                "ORDER BY category COLLATE NOCASE, subcategory COLLATE NOCASE"
            ).fetchall()
        except Exception:
            log.exception("load_taxonomy: DB error; falling back to FIXED_TAXONOMY")
            return dict(_SEED_TAXONOMY)

        if not rows:
            return dict(_SEED_TAXONOMY)

        taxonomy: dict[str, list[str]] = {}
        for r in rows:
            cat = r["category"]
            sub = r["subcategory"]
            if cat not in taxonomy:
                taxonomy[cat] = []
            if sub:
                taxonomy[cat].append(sub)
        return taxonomy

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 10)
        outer.setSpacing(8)

        outer.addWidget(QLabel(
            "Add, rename, or remove categories and subcategories.\n"
            "Changes apply immediately to the database and to all books."
        ))

        # Tree ---------------------------------------------------------------
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("Categories")
        self._tree.setRootIsDecorated(True)
        self._tree.setAnimated(True)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._sync_buttons)
        self._tree.itemDoubleClicked.connect(self._on_edit)
        outer.addWidget(self._tree)

        # Action buttons -----------------------------------------------------
        act = QHBoxLayout()

        self._btn_add_top = QPushButton("+ Add Category")
        self._btn_add_top.setToolTip("Add a new top-level category")
        self._btn_add_top.clicked.connect(self._on_add_top)
        act.addWidget(self._btn_add_top)

        self._btn_add_sub = QPushButton("+ Add Subcategory")
        self._btn_add_sub.setToolTip("Add a subcategory under the selected category")
        self._btn_add_sub.setEnabled(False)
        self._btn_add_sub.clicked.connect(self._on_add_sub)
        act.addWidget(self._btn_add_sub)

        act.addStretch()

        self._btn_edit = QPushButton("Rename")
        self._btn_edit.setEnabled(False)
        self._btn_edit.clicked.connect(self._on_edit)
        act.addWidget(self._btn_edit)

        self._btn_delete = QPushButton("Delete")
        self._btn_delete.setEnabled(False)
        self._btn_delete.setObjectName("deleteBtn")
        self._btn_delete.setStyleSheet("QPushButton#deleteBtn { color: #c0392b; }")
        self._btn_delete.clicked.connect(self._on_delete)
        act.addWidget(self._btn_delete)

        outer.addLayout(act)

        # Close button -------------------------------------------------------
        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        close_row.addWidget(btn_close)
        outer.addLayout(close_row)

    # ------------------------------------------------------------------
    # Seeding / initial load
    # ------------------------------------------------------------------

    def _load_or_seed(self) -> None:
        try:
            conn = get_conn(self._db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM canonical_categories"
            ).fetchone()[0]

            if count == 0 and _SEED_TAXONOMY:
                log.info("canonical_categories is empty — seeding from FIXED_TAXONOMY")
                self._seed(conn)

        except Exception as exc:
            log.exception("Failed to load/seed canonical_categories")
            QMessageBox.critical(self, "Database Error",
                                 f"Could not load categories:\n\n{exc}")
            return

        self._rebuild_tree()

    def _seed(self, conn) -> None:
        """Insert all FIXED_TAXONOMY entries as (category, subcategory) rows."""
        pairs: list[tuple[str, str]] = []
        for cat, subs in _SEED_TAXONOMY.items():
            pairs.append((cat, ""))
            for sub in subs:
                pairs.append((cat, sub))
        conn.executemany(
            "INSERT OR IGNORE INTO canonical_categories (category, subcategory) VALUES (?,?)",
            pairs,
        )
        conn.commit()
        log.info("Seeded %d canonical_categories rows", len(pairs))

    # ------------------------------------------------------------------
    # Tree
    # ------------------------------------------------------------------

    def _rebuild_tree(self) -> None:
        """Re-read canonical_categories and reconstruct the QTreeWidget."""
        # Remember which top-level nodes were expanded
        expanded: set[str] = {
            self._tree.topLevelItem(i).text(0)
            for i in range(self._tree.topLevelItemCount())
            if self._tree.topLevelItem(i) and self._tree.topLevelItem(i).isExpanded()
        }

        self._tree.blockSignals(True)
        self._tree.clear()

        try:
            conn = get_conn(self._db_path)
            rows = conn.execute(
                "SELECT id, category, subcategory "
                "FROM canonical_categories "
                "ORDER BY category COLLATE NOCASE, subcategory COLLATE NOCASE"
            ).fetchall()
        except Exception as exc:
            log.exception("Failed to read canonical_categories")
            self._tree.blockSignals(False)
            QMessageBox.critical(self, "Database Error", str(exc))
            return

        # Group: category_name -> list of (id, subcategory)
        grouped: dict[str, list[tuple[int, str]]] = {}
        for r in rows:
            grouped.setdefault(r["category"], []).append((r["id"], r["subcategory"]))

        for cat_name in sorted(grouped, key=str.lower):
            entries = grouped[cat_name]

            # The top-level item represents the category itself.
            # Its DB id is the row where subcategory='', if that row exists.
            own_id: int | None = next(
                (rid for rid, sub in entries if sub == ""), None
            )
            top = _make_item(cat_name, {"type": "category", "name": cat_name, "id": own_id})

            # Children: subcategory rows (skip the '' placeholder row)
            for row_id, sub in sorted(
                ((rid, s) for rid, s in entries if s), key=lambda x: x[1].lower()
            ):
                child = _make_item(
                    sub,
                    {"type": "subcategory", "id": row_id,
                     "name": sub, "category": cat_name},
                )
                top.addChild(child)

            self._tree.addTopLevelItem(top)
            if cat_name in expanded:
                top.setExpanded(True)

        self._tree.blockSignals(False)
        self._sync_buttons()

    # ------------------------------------------------------------------
    # Button state
    # ------------------------------------------------------------------

    def _sync_buttons(self) -> None:
        item = self._tree.currentItem()
        has_sel       = item is not None
        is_top        = has_sel and item.parent() is None

        self._btn_add_sub.setEnabled(is_top)
        self._btn_edit.setEnabled(has_sel)
        self._btn_delete.setEnabled(has_sel)

    # ------------------------------------------------------------------
    # Add top-level category
    # ------------------------------------------------------------------

    def _on_add_top(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Category", "Category name:")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Name cannot be empty.")
            return

        try:
            conn = get_conn(self._db_path)
            if conn.execute(
                "SELECT 1 FROM canonical_categories WHERE category=? AND subcategory=''",
                (name,)
            ).fetchone():
                QMessageBox.warning(self, "Duplicate",
                                    f'Category "{name}" already exists.')
                return

            conn.execute(
                "INSERT INTO canonical_categories (category, subcategory) VALUES (?,'')",
                (name,)
            )
            conn.commit()
            log.info("Added category: %s", name)

        except Exception as exc:
            log.exception("Failed to add category")
            QMessageBox.critical(self, "Error", str(exc))
            return

        self._rebuild_tree()
        self._scroll_to(name, parent_name=None)
        self.categories_updated.emit()

    # ------------------------------------------------------------------
    # Add subcategory
    # ------------------------------------------------------------------

    def _on_add_sub(self) -> None:
        item = self._tree.currentItem()
        if item is None or item.parent() is not None:
            return

        parent_name = item.text(0)
        name, ok = QInputDialog.getText(
            self, "Add Subcategory",
            f"Subcategory under \"{parent_name}\":",
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Name cannot be empty.")
            return

        try:
            conn = get_conn(self._db_path)
            if conn.execute(
                "SELECT 1 FROM canonical_categories WHERE category=? AND subcategory=?",
                (parent_name, name)
            ).fetchone():
                QMessageBox.warning(self, "Duplicate",
                                    f'"{name}" already exists under "{parent_name}".')
                return

            conn.execute(
                "INSERT INTO canonical_categories (category, subcategory) VALUES (?,?)",
                (parent_name, name)
            )
            conn.commit()
            log.info("Added subcategory: %s > %s", parent_name, name)

        except Exception as exc:
            log.exception("Failed to add subcategory")
            QMessageBox.critical(self, "Error", str(exc))
            return

        self._rebuild_tree()
        self._scroll_to(name, parent_name=parent_name)
        self.categories_updated.emit()

    # ------------------------------------------------------------------
    # Rename (Edit)
    # ------------------------------------------------------------------

    def _on_edit(self, *_) -> None:
        item = self._tree.currentItem()
        if item is None:
            return

        data: dict = item.data(0, _ROLE) or {}
        old_name = data.get("name", "")

        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=old_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return

        try:
            conn = get_conn(self._db_path)

            if data["type"] == "category":
                # Check for name collision before renaming
                if conn.execute(
                    "SELECT 1 FROM canonical_categories WHERE category=? AND subcategory=''",
                    (new_name,)
                ).fetchone():
                    QMessageBox.warning(self, "Duplicate",
                                        f'Category "{new_name}" already exists.')
                    return

                # Rename all rows (top-level placeholder + all its subcategory rows)
                conn.execute(
                    "UPDATE canonical_categories SET category=? WHERE category=?",
                    (new_name, old_name)
                )
                # Keep denormalised books columns in sync
                conn.execute(
                    "UPDATE books SET category=? WHERE category=?",
                    (new_name, old_name)
                )
                conn.commit()
                log.info("Renamed category: %s -> %s", old_name, new_name)

            else:   # subcategory
                parent_cat = data.get("category", "")
                row_id     = data.get("id")

                if conn.execute(
                    "SELECT 1 FROM canonical_categories WHERE category=? AND subcategory=?",
                    (parent_cat, new_name)
                ).fetchone():
                    QMessageBox.warning(self, "Duplicate",
                                        f'"{new_name}" already exists under "{parent_cat}".')
                    return

                conn.execute(
                    "UPDATE canonical_categories SET subcategory=? WHERE id=?",
                    (new_name, row_id)
                )
                conn.execute(
                    "UPDATE books SET subcategory=? WHERE category=? AND subcategory=?",
                    (new_name, parent_cat, old_name)
                )
                conn.commit()
                log.info("Renamed subcategory: %s > %s -> %s", parent_cat, old_name, new_name)

        except Exception as exc:
            log.exception("Failed to rename")
            QMessageBox.critical(self, "Error", str(exc))
            return

        self._rebuild_tree()
        parent = data.get("category") if data["type"] == "subcategory" else None
        self._scroll_to(new_name, parent_name=parent)
        self.categories_updated.emit()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete(self) -> None:
        item = self._tree.currentItem()
        if item is None:
            return

        data: dict = item.data(0, _ROLE) or {}
        name = data.get("name", "")

        if data["type"] == "category":
            n_children = item.childCount()
            child_note = (
                f"\n\nThis will also remove its {n_children} "
                f"subcategor{'y' if n_children == 1 else 'ies'}."
                if n_children else ""
            )
            msg = (
                f'Delete category "{name}"?{child_note}\n\n'
                "All books in this category will have their category cleared."
            )
        else:
            parent_cat = data.get("category", "")
            msg = (
                f'Delete subcategory "{name}" from "{parent_cat}"?\n\n'
                "Books in this subcategory will have their subcategory cleared."
            )

        if QMessageBox.question(
            self, "Confirm Delete", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = get_conn(self._db_path)

            if data["type"] == "category":
                # Deletes the '' row + all subcategory rows for this category.
                # document_categories rows cascade automatically.
                conn.execute(
                    "DELETE FROM canonical_categories WHERE category=?", (name,)
                )
                conn.execute(
                    "UPDATE books SET category=NULL, subcategory=NULL WHERE category=?",
                    (name,)
                )
                conn.commit()
                log.info("Deleted category: %s", name)

            else:
                parent_cat = data.get("category", "")
                row_id     = data.get("id")
                conn.execute(
                    "DELETE FROM canonical_categories WHERE id=?", (row_id,)
                )
                conn.execute(
                    "UPDATE books SET subcategory=NULL WHERE category=? AND subcategory=?",
                    (parent_cat, name)
                )
                conn.commit()
                log.info("Deleted subcategory: %s > %s", parent_cat, name)

        except Exception as exc:
            log.exception("Failed to delete")
            QMessageBox.critical(self, "Error", str(exc))
            return

        self._rebuild_tree()
        self.categories_updated.emit()

    # ------------------------------------------------------------------
    # Tree selection helper
    # ------------------------------------------------------------------

    def _scroll_to(self, name: str, parent_name: str | None) -> None:
        """Select and scroll to the named item after a tree rebuild."""
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top is None:
                continue

            if parent_name is None:
                if top.text(0) == name:
                    self._tree.setCurrentItem(top)
                    self._tree.scrollToItem(top)
                    return
            elif top.text(0) == parent_name:
                top.setExpanded(True)
                for j in range(top.childCount()):
                    child = top.child(j)
                    if child and child.text(0) == name:
                        self._tree.setCurrentItem(child)
                        self._tree.scrollToItem(child)
                        return


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _make_item(label: str, payload: dict) -> QTreeWidgetItem:
    item = QTreeWidgetItem([label])
    item.setData(0, _ROLE, payload)
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
    return item
