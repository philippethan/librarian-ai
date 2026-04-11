"""
ui/dialogs/rename_file_dialog.py — Rename-file dialog with filename validation.

Public surface
--------------
generate_filename(title, author, year, ext) -> str
    Pure function.  Build a sanitised, filesystem-safe filename suggestion.
    Format: title_by_author_year.ext (lowercase, underscores).
    Tests 3.2, 3.3, 3.14, 3.15, 3.16.

validate_filename(name) -> tuple[bool, str]
    Pure function.  All validation rules in one place — importable by tests
    without instantiating any Qt widget.
    Tests 3.6, 3.7, 3.8, 3.9.

RenameFileDialog(current_filename, suggested_filename, original_ext, parent)
    QDialog subclass.
    Tests 3.1, 3.2, 3.6 – 3.10.
    After exec() == Accepted call get_new_filename() -> str.

Caller pattern (main_window.py)
--------------------------------
    from ui.dialogs.rename_file_dialog import RenameFileDialog, generate_filename

    suggested = generate_filename(
        book["title"], book["author"], book["year"],
        Path(book["filepath"]).suffix,
    )
    dlg = RenameFileDialog(
        current_filename   = book["filename"],
        suggested_filename = suggested,
        original_ext       = Path(book["filepath"]).suffix,
        parent             = self,
    )
    if dlg.exec():
        new_name = dlg.get_new_filename()
        # … rename on disk, then UPDATE books SET filename=?, filepath=? WHERE id=?
"""

import os
import re
import unicodedata

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Characters forbidden in Windows / macOS / Linux filenames.
INVALID_CHARS: frozenset[str] = frozenset(r'/\:*?"<>|')

#: Human-readable list shown in the validation error message (Test 3.7).
_INVALID_CHARS_DISPLAY = r'\ / : * ? " < > |'

#: Regex that strips forbidden chars from a generated suggestion.
_STRIP_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


# ---------------------------------------------------------------------------
# Pure validation — no Qt dependency, directly importable by test runners
# ---------------------------------------------------------------------------

def validate_filename(name: str) -> tuple[bool, str]:
    """Return (is_valid, error_message).

    Checks are applied in the order listed; the first failure wins.

    Rule 1 — empty                (Test 3.6)
    Rule 2 — path traversal       (Test 3.8)
    Rule 3 — forbidden characters (Test 3.7)
    Rule 4 — extension present    (Test 3.9)
    Rule 5 — base name non-empty
    """
    stripped = name.strip()

    # Rule 1: empty
    if not stripped:
        return False, "Filename cannot be empty"

    # Rule 2: path traversal — checked before individual chars so ".." and
    # "/" trigger this rule even though "/" is also in INVALID_CHARS.
    if ".." in stripped or "/" in stripped or "\\" in stripped:
        return False, r"Cannot contain paths (.., /, \)"

    # Rule 3: forbidden characters
    if INVALID_CHARS & set(stripped):
        return False, f"Contains invalid characters:  {_INVALID_CHARS_DISPLAY}"

    # Rule 4: extension present
    base, ext = os.path.splitext(stripped)
    if not ext:
        return False, "Must include file extension"

    # Rule 5: base name non-empty (e.g. ".pdf" alone has no base)
    if not base.strip():
        return False, "Filename cannot be empty"

    return True, ""


# ---------------------------------------------------------------------------
# Filename suggestion — pure, no Qt dependency
# ---------------------------------------------------------------------------

def generate_filename(
    title:  str | None,
    author: str | None,
    year:   str | int | None,
    ext:    str,
) -> str:
    """Build a sanitised, filesystem-safe suggested filename.

    Format: ``title_by_author_year.ext`` (lowercase, underscores).

    Missing or blank fields are skipped gracefully.  Falls back to
    ``"unnamed"`` when no usable fields are present.

    Examples (Test 3.2, 3.3, 3.14, 3.15, 3.16):
        generate_filename("Machine Learning", "Ng", "2023", ".pdf")
        → "machine_learning_by_ng_2023.pdf"

        generate_filename("AI: The Future", "John/Doe", "2020", ".pdf")
        → "ai_the_future_by_johndoe_2020.pdf"

        generate_filename("PYTHON Programming", "Guido VAN Rossum", None, ".pdf")
        → "python_programming_by_guido_van_rossum.pdf"

        generate_filename("Машинное обучение", None, None, ".pdf")
        → falls back to romanised/stripped form or "unnamed.pdf" — never crashes
    """

    def _sanitise(raw: str) -> str:
        # NFKC normalisation helps with accented / CJK / Cyrillic chars.
        # Characters that survive the unicode round-trip stay; those that
        # don't are stripped, satisfying Test 3.16 (no crash on unicode).
        normalised = unicodedata.normalize("NFKC", raw)
        # Strip filesystem-forbidden chars
        cleaned = _STRIP_RE.sub("", normalised)
        # Lower-case, collapse all whitespace/dashes/dots to a single space
        cleaned = re.sub(r"[\s\-.]+" , " ", cleaned).strip().lower()
        # Space → underscore, then collapse repeated underscores
        cleaned = re.sub(r"\s+", "_", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned)
        return cleaned.strip("_")

    parts: list[str] = []

    if title:
        slug = _sanitise(str(title))
        if slug:
            parts.append(slug)

    if author:
        slug = _sanitise(str(author))
        if slug:
            parts.append(f"by_{slug}")

    if year:
        slug = _sanitise(str(year))
        if slug:
            parts.append(slug)

    base = "_".join(parts) if parts else "unnamed"

    # Normalise extension: ensure it starts with exactly one dot
    ext = ext.strip()
    if ext and not ext.startswith("."):
        ext = "." + ext

    return base + ext


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class RenameFileDialog(QDialog):
    """Modal dialog to rename a book file.

    Parameters
    ----------
    current_filename:
        The existing filename shown read-only (Test 3.1).
    suggested_filename:
        Pre-filled editable name; usually built by ``generate_filename()``.
        Must already include the extension so Test 3.10 (extension preserved)
        is satisfied out of the box.
    original_ext:
        Extension of the source file (e.g. ``".pdf"``).  Shown as a hint and
        used by the "Restore extension" shortcut button.
    """

    def __init__(
        self,
        current_filename:   str,
        suggested_filename: str,
        original_ext:       str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._current   = current_filename
        self._suggested = suggested_filename
        # Normalise: always lower-case, always starts with a dot (or empty)
        self._orig_ext  = (
            ("." + original_ext.lstrip(".")).lower()
            if original_ext.strip()
            else ""
        )

        self.setWindowTitle("Rename File")
        self.setMinimumWidth(500)
        self.resize(540, 0)   # height is auto-fitted by layout
        self.setModal(True)
        self.setSizeGripEnabled(True)

        self._build_ui()
        # Run validation once so the Rename button starts in the correct state
        self._on_text_changed(self._input.text())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # Current filename (read-only) ------------------------------------
        outer.addWidget(_section_label("Current filename"))

        self._current_display = QLineEdit(self._current)
        self._current_display.setReadOnly(True)
        self._current_display.setStyleSheet("color: palette(mid);")
        self._current_display.setToolTip("The existing filename — cannot be edited here")
        outer.addWidget(self._current_display)

        # New filename (editable) -----------------------------------------
        outer.addWidget(_section_label("New filename"))

        self._input = QLineEdit(self._suggested)
        self._input.setToolTip("Edit the new filename.  The extension must be included.")
        self._input.textChanged.connect(self._on_text_changed)
        outer.addWidget(self._input)

        # Validation feedback (Test 3.6 – 3.9) ----------------------------
        self._validation_label = QLabel()
        # Reserve one line of height so the layout doesn't jump
        self._validation_label.setMinimumHeight(
            self._validation_label.fontMetrics().height() + 4
        )
        outer.addWidget(self._validation_label)

        # Separator --------------------------------------------------------
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        # Hints panel ------------------------------------------------------
        ext_display = self._orig_ext or "(none detected)"
        hint_html = (
            f"<small>"
            f"<b>Original extension:</b>&nbsp; <tt>{ext_display}</tt><br>"
            f"Forbidden characters: &nbsp;<tt>\\ &nbsp;/ &nbsp;: &nbsp;* &nbsp;"
            f"? &nbsp;&quot; &nbsp;&lt; &nbsp;&gt; &nbsp;|</tt><br>"
            f"Path components not allowed: &nbsp;<tt>.. &nbsp; / &nbsp; \\</tt>"
            f"</small>"
        )
        hints = QLabel(hint_html)
        hints.setTextFormat(Qt.TextFormat.RichText)
        hints.setStyleSheet("color: palette(mid);")
        outer.addWidget(hints)

        # Restore-extension button (Test 3.10) ----------------------------
        if self._orig_ext:
            restore_row = QHBoxLayout()
            self._restore_btn = QPushButton(
                f"Restore original extension  ({self._orig_ext})"
            )
            self._restore_btn.setToolTip(
                f"Replace the current extension with {self._orig_ext}"
            )
            self._restore_btn.clicked.connect(self._on_restore_extension)
            restore_row.addWidget(self._restore_btn)
            restore_row.addStretch()
            outer.addLayout(restore_row)

        # Buttons ----------------------------------------------------------
        outer.addSpacing(4)
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._rename_btn = QPushButton("Rename")
        self._rename_btn.setDefault(True)
        self._rename_btn.clicked.connect(self._on_rename_clicked)
        btn_row.addWidget(self._rename_btn)

        outer.addLayout(btn_row)

        # Select all text so the user can start typing immediately
        self._input.setFocus()
        self._input.selectAll()

    # ------------------------------------------------------------------
    # Live validation — called on every keystroke
    # ------------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        ok, msg = validate_filename(text)

        if ok:
            self._validation_label.setText("✓  Valid filename")
            self._validation_label.setStyleSheet(
                "color: #27ae60; font-weight: bold;"
            )
        else:
            self._validation_label.setText(f"✗  {msg}")
            self._validation_label.setStyleSheet(
                "color: #c0392b; font-weight: bold;"
            )

        # Rename button is only enabled for valid names (Test 3.6 – 3.9)
        self._rename_btn.setEnabled(ok)

    # ------------------------------------------------------------------
    # Restore-extension shortcut (Test 3.10)
    # ------------------------------------------------------------------

    def _on_restore_extension(self) -> None:
        """Strip whatever extension is currently in the input and append
        the original extension.  Handles the common case where the user
        accidentally removed or changed the extension."""
        text = self._input.text().strip()
        base, _ = os.path.splitext(text)
        # Use the stripped base — if it's empty, keep the raw text
        restored = (base or text) + self._orig_ext
        self._input.setText(restored)
        self._input.setFocus()
        # Place cursor at the end of the base name, before the extension,
        # so further typing doesn't accidentally clobber the extension
        self._input.setCursorPosition(len(base or text))

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _on_rename_clicked(self) -> None:
        ok, _ = validate_filename(self._input.text())
        if ok:
            self.accept()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_new_filename(self) -> str:
        """Return the validated filename entered by the user.

        Call only after ``exec()`` returned ``QDialog.DialogCode.Accepted``.
        """
        return self._input.text().strip()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: bold;")
    return lbl
