"""
ui/dialogs/online_lookup_dialog.py

Two-phase "Online Lookup + Ollama Categorize" dialog.

Phase 1 (background) - fans out to Open Library, Google Books, CrossRef,
    Internet Archive, and OpenAlex via _collect_candidates().  Shows ranked
    candidates in a list; user picks one.

Phase 2 (background) - takes the selected candidate's subjects + description
    merged with the book's own metadata and calls call_ollama_categorize().
    Displays the suggested category / subcategory.

Apply - writes merged metadata + category back to the DB in one UPDATE.
"""

import json
import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.db import get_conn
from backend.app import (
    FIXED_TAXONOMY,
    _collect_candidates,
    _map_to_fixed_category,
    _norm,
    _parse_filename,
    call_ollama_categorize,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _LookupWorker(QThread):
    """Fetch candidates from all online sources (runs off the main thread)."""

    finished = pyqtSignal(list)   # list[dict]
    error    = pyqtSignal(str)

    def __init__(self, title: str, author: str, year: str, filepath: str, parent=None) -> None:
        super().__init__(parent)
        self._title    = title
        self._author   = author
        self._year     = year
        self._filepath = filepath

    def run(self) -> None:
        try:
            fn_title, fn_author = _parse_filename(self._filepath or "")

            title  = self._title  or fn_title
            author = self._author or fn_author

            # Secondary query: filename-derived values when meaningfully different
            alt_title  = fn_title  if (fn_title  and _norm(fn_title)  != _norm(title))  else ""
            alt_author = fn_author if (fn_author and _norm(fn_author) != _norm(author)) else ""

            candidates = _collect_candidates(
                title, author,
                year=self._year,
                alt_title=alt_title,
                alt_author=alt_author,
            )
            self.finished.emit(candidates)
        except Exception as exc:  # noqa: BLE001
            log.exception("LookupWorker error")
            self.error.emit(str(exc))


class _CategorizeWorker(QThread):
    """Call Ollama to get category / subcategory (runs off the main thread)."""

    finished = pyqtSignal(dict)   # {'category': str, 'subcategory': str}
    error    = pyqtSignal(str)

    def __init__(
        self,
        title: str,
        author: str,
        description: str,
        tags: list[str],
        language: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._title       = title
        self._author      = author
        self._description = description
        self._tags        = tags
        self._language    = language

    def run(self) -> None:
        try:
            result = call_ollama_categorize(
                self._title,
                self._author,
                self._description,
                self._tags,
                self._language,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("CategorizeWorker error")
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class OnlineLookupDialog(QDialog):
    """
    Opens modally from BookDetailDialog.  On apply, writes merged metadata
    (title/author/year/language/description/tags + Ollama category) to the DB
    and emits `applied` so the parent can refresh its form fields.

    Usage::

        dlg = OnlineLookupDialog(book_id, book_dict, db_path, parent=self)
        dlg.applied.connect(self._on_lookup_applied)
        dlg.exec()
    """

    applied = pyqtSignal(dict)   # updated field dict emitted after DB write

    def __init__(
        self,
        book_id: int,
        book: dict,
        db_path: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._book_id  = book_id
        self._book     = book         # current DB record (read-only reference)
        self._db_path  = db_path

        self._candidates: list[dict] = []
        self._selected_candidate: dict | None = None
        self._ollama_category:    str = ""
        self._ollama_subcategory: str = ""

        self._lookup_worker:     _LookupWorker     | None = None
        self._categorize_worker: _CategorizeWorker | None = None

        self.setWindowTitle("Online Lookup & Categorize")
        self.setMinimumSize(780, 600)
        self.resize(860, 680)
        self.setModal(True)

        self._build_ui()
        # Start searching as soon as the dialog is created
        self._start_lookup()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 10)
        outer.setSpacing(8)

        # ---- search status row ----
        status_row = QHBoxLayout()
        self._search_label = QLabel("Searching online sources…")
        self._search_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        status_row.addWidget(self._search_label)
        outer.addLayout(status_row)

        self._progress = QProgressBar()
        self._progress.setMaximum(0)   # indeterminate while searching
        self._progress.setTextVisible(False)
        outer.addWidget(self._progress)

        # ---- candidate list (left) + details (right) ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(QLabel("Candidates found:"))
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setAlternatingRowColors(True)
        self._list.currentRowChanged.connect(self._on_candidate_selected)
        left_layout.addWidget(self._list)
        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(4)
        right_layout.addWidget(QLabel("Selected candidate details:"))
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setAcceptRichText(True)
        right_layout.addWidget(self._detail)
        splitter.addWidget(right_widget)

        splitter.setSizes([290, 520])
        outer.addWidget(splitter, stretch=1)

        # ---- Ollama section ----
        ollama_box = QGroupBox("Ollama Categorization")
        ollama_layout = QVBoxLayout(ollama_box)
        ollama_layout.setSpacing(6)

        top_row = QHBoxLayout()
        self._categorize_btn = QPushButton("Categorize with Ollama")
        self._categorize_btn.setEnabled(False)
        self._categorize_btn.setToolTip(
            "Send the selected candidate's subjects and description to the local Ollama model\n"
            "to determine the best category and subcategory from the library taxonomy."
        )
        self._categorize_btn.clicked.connect(self._on_categorize_clicked)
        top_row.addWidget(self._categorize_btn)
        top_row.addStretch()
        ollama_layout.addLayout(top_row)

        self._ollama_status = QLabel(
            "Select a candidate above, then click \"Categorize with Ollama\"."
        )
        self._ollama_status.setWordWrap(True)
        ollama_layout.addWidget(self._ollama_status)

        self._ollama_result_label = QLabel("")
        self._ollama_result_label.setWordWrap(True)
        self._ollama_result_label.setStyleSheet("font-weight: bold; color: #1a6faf;")
        ollama_layout.addWidget(self._ollama_result_label)

        outer.addWidget(ollama_box)

        # ---- bottom button row ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._apply_btn = QPushButton("Apply to Book")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setDefault(True)
        self._apply_btn.setToolTip(
            "Write the selected candidate's metadata and Ollama category to the database."
        )
        self._apply_btn.clicked.connect(self._on_apply_clicked)
        btn_row.addWidget(self._apply_btn)

        outer.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Phase 1 — Online lookup
    # ------------------------------------------------------------------

    def _start_lookup(self) -> None:
        book = self._book
        self._lookup_worker = _LookupWorker(
            title    = (book.get("title")  or "").strip(),
            author   = (book.get("author") or "").strip(),
            year     = str(book.get("year") or "").strip(),
            filepath = (book.get("filepath") or ""),
            parent   = self,
        )
        self._lookup_worker.finished.connect(self._on_lookup_finished)
        self._lookup_worker.error.connect(self._on_lookup_error)
        self._lookup_worker.start()

    def _on_lookup_finished(self, candidates: list[dict]) -> None:
        self._candidates = candidates
        self._progress.setMaximum(1)
        self._progress.setValue(1)

        if not candidates:
            self._search_label.setText(
                "No results found. Check title / author and try editing the book first."
            )
            return

        self._search_label.setText(
            f"Found {len(candidates)} candidate(s). Select one to see details."
        )
        self._list.clear()
        for c in candidates:
            sources = ", ".join(c.get("sources") or [c.get("source", "?")])
            label = (
                f"{c.get('title', '—')}  ·  "
                f"{c.get('author', '—')}  ·  "
                f"{c.get('year', '—')}  "
                f"[{sources}]"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self._list.addItem(item)

        # Auto-select the top-ranked candidate
        self._list.setCurrentRow(0)

    def _on_lookup_error(self, message: str) -> None:
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self._search_label.setText(f"Lookup failed: {message}")

    # ------------------------------------------------------------------
    # Candidate selection → details panel
    # ------------------------------------------------------------------

    def _on_candidate_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._candidates):
            self._selected_candidate = None
            self._categorize_btn.setEnabled(False)
            self._detail.clear()
            return

        candidate = self._candidates[row]
        self._selected_candidate = candidate

        # Reset Ollama result when the user picks a different candidate
        self._ollama_category    = ""
        self._ollama_subcategory = ""
        self._ollama_result_label.setText("")
        self._ollama_status.setText(
            'Click "Categorize with Ollama" to suggest a category for this candidate.'
        )
        self._apply_btn.setEnabled(True)     # can apply even without Ollama
        self._categorize_btn.setEnabled(True)

        self._detail.setHtml(self._format_candidate(candidate))

    @staticmethod
    def _format_candidate(c: dict) -> str:
        def row(label: str, value: str) -> str:
            if not value:
                return ""
            return f"<tr><td><b>{label}</b></td><td>&nbsp;&nbsp;{value}</td></tr>"

        sources   = ", ".join(c.get("sources") or [c.get("source", "")])
        subjects  = ", ".join((c.get("subjects") or [])[:15]) or "—"
        desc      = (c.get("description") or "").strip()
        desc_html = f"<p>{desc}</p>" if desc else ""

        table = (
            "<table cellspacing='4'>"
            + row("Title",    c.get("title",    "") or "—")
            + row("Author",   c.get("author",   "") or "—")
            + row("Year",     c.get("year",     "") or "—")
            + row("Language", c.get("language", "") or "—")
            + row("Sources",  sources or "—")
            + row("Subjects", subjects)
            + "</table>"
        )
        return table + desc_html

    # ------------------------------------------------------------------
    # Phase 2 — Ollama categorization
    # ------------------------------------------------------------------

    def _on_categorize_clicked(self) -> None:
        if self._selected_candidate is None:
            return

        c    = self._selected_candidate
        book = self._book

        # Build the richest possible inputs by merging candidate + existing book
        title       = c.get("title")       or book.get("title")       or ""
        author      = c.get("author")      or book.get("author")      or ""
        language    = c.get("language")    or book.get("language")    or ""
        description = c.get("description") or book.get("description") or ""

        # Tags: candidate subjects take priority
        subjects = c.get("subjects") or []
        existing_tags: list[str] = book.get("tags") or []
        tags = subjects[:15] or existing_tags

        self._categorize_btn.setEnabled(False)
        self._progress.setMaximum(0)   # indeterminate again
        self._ollama_status.setText("Asking Ollama for category suggestion…")
        self._ollama_result_label.setText("")

        self._categorize_worker = _CategorizeWorker(
            title=title,
            author=author,
            description=description,
            tags=tags,
            language=language,
            parent=self,
        )
        self._categorize_worker.finished.connect(self._on_categorize_finished)
        self._categorize_worker.error.connect(self._on_categorize_error)
        self._categorize_worker.start()

    def _on_categorize_finished(self, result: dict) -> None:
        self._progress.setMaximum(1)
        self._progress.setValue(1)
        self._categorize_btn.setEnabled(True)

        def _s(v) -> str:
            if isinstance(v, list):
                return ", ".join(str(x) for x in v if x)
            return str(v).strip() if v else ""

        category    = _s(result.get("category"))
        subcategory = _s(result.get("subcategory"))

        # Validate against FIXED_TAXONOMY — same logic as backend._categorize_book_by_llm
        if category not in FIXED_TAXONOMY:
            # Try keyword fallback from candidate subjects
            subjects = (self._selected_candidate or {}).get("subjects") or []
            if subjects:
                category, subcategory = _map_to_fixed_category(subjects)
            else:
                category    = "Miscellaneous"
                subcategory = "Other"
        elif subcategory not in FIXED_TAXONOMY.get(category, []):
            subcategory = FIXED_TAXONOMY[category][0] if FIXED_TAXONOMY[category] else ""

        self._ollama_category    = category
        self._ollama_subcategory = subcategory

        self._ollama_status.setText("Ollama suggestion:")
        self._ollama_result_label.setText(
            f"Category: {category}   /   Subcategory: {subcategory}"
        )
        self._apply_btn.setEnabled(True)
        log.info(
            "Ollama categorized book id=%d → %r / %r",
            self._book_id, category, subcategory,
        )

    def _on_categorize_error(self, message: str) -> None:
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self._categorize_btn.setEnabled(True)
        self._ollama_status.setText(
            f"Ollama failed: {message}\n"
            "You can still apply the candidate metadata without a category suggestion."
        )
        log.warning("CategorizeWorker error: %s", message)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _on_apply_clicked(self) -> None:
        if self._selected_candidate is None:
            QMessageBox.warning(self, "No Candidate Selected",
                                "Please select a candidate from the list first.")
            return

        c    = self._selected_candidate
        book = self._book

        # Merge: non-empty candidate values win over existing book values
        def _pick(candidate_val, book_val):
            v = (candidate_val or "").strip()
            return v if v else (book_val or "").strip()

        title       = _pick(c.get("title"),       book.get("title"))
        author      = _pick(c.get("author"),       book.get("author"))
        year        = _pick(c.get("year"),         book.get("year"))
        language    = _pick(c.get("language"),     book.get("language"))
        description = _pick(c.get("description"),  book.get("description"))

        # Tags: candidate subjects → clean list (cap at 12, skip very long strings)
        subjects = c.get("subjects") or []
        tags_list = [s.strip() for s in subjects[:12] if s.strip() and len(s) < 60]
        tags_json = json.dumps(tags_list, ensure_ascii=True)

        # Category: use Ollama result if available; else derive from subjects
        if self._ollama_category:
            category    = self._ollama_category
            subcategory = self._ollama_subcategory
        elif subjects:
            category, subcategory = _map_to_fixed_category(subjects)
        else:
            category    = book.get("category")    or ""
            subcategory = book.get("subcategory") or ""

        method = "online"
        if self._ollama_category:
            method = "online+ollama"

        sources = ", ".join(c.get("sources") or [c.get("source", "?")])

        try:
            conn = get_conn(self._db_path)
            conn.execute(
                """
                UPDATE books SET
                    title              = COALESCE(NULLIF(?, ''), title),
                    author             = COALESCE(NULLIF(?, ''), author),
                    year               = COALESCE(NULLIF(?, ''), year),
                    language           = COALESCE(NULLIF(?, ''), language),
                    description        = COALESCE(NULLIF(?, ''), description),
                    tags               = ?,
                    category           = COALESCE(NULLIF(?, ''), category),
                    subcategory        = COALESCE(NULLIF(?, ''), subcategory),
                    fixed_category     = COALESCE(NULLIF(?, ''), fixed_category),
                    fixed_subcategory  = COALESCE(NULLIF(?, ''), fixed_subcategory),
                    status             = 'done',
                    confidence_score   = 1.0,
                    extraction_method  = ?,
                    processed_at       = datetime('now')
                WHERE id = ?
                """,
                (
                    title, author, year, language, description,
                    tags_json,
                    category, subcategory,
                    category, subcategory,    # fixed_category / fixed_subcategory
                    f"{method}:{sources}",
                    self._book_id,
                ),
            )
            conn.commit()
            log.info(
                "Applied online lookup for book id=%d  title=%r  cat=%r/%r  method=%s",
                self._book_id, title, category, subcategory, method,
            )
        except Exception as exc:
            log.exception("Failed to apply lookup for book id=%d", self._book_id)
            QMessageBox.critical(self, "Apply Failed", f"Could not save to database:\n\n{exc}")
            return

        # Emit updated fields so the parent dialog can refresh without a full DB reload
        self.applied.emit({
            "title":       title,
            "author":      author,
            "year":        year,
            "language":    language,
            "description": description,
            "tags":        tags_list,
            "category":    category,
            "subcategory": subcategory,
        })
        self.accept()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        for w in (self._lookup_worker, self._categorize_worker):
            if w is not None and w.isRunning():
                w.wait(2_000)
        super().closeEvent(event)
