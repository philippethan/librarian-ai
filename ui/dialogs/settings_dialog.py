"""
ui/dialogs/settings_dialog.py — Application settings dialog.

Two sections
────────────
Application Window Settings
    (Currently the dark theme is applied globally; future options live here.)

Document Information Settings
    • Khmer font family — font used for title / author / filename when those
      fields contain Khmer characters (U+1780–U+17FF).  All other text uses
      the dark-theme design font (Segoe UI).
    • Document font size — size of book-info text in list view and card view
      (8–18 pt).  Does NOT change the rest of the UI.
    • Cover resolution — how large cover images are scaled before caching
      (Small 96 px · Medium 140 px · Large 178 px).

Settings are persisted via QSettings("LibrarianAI", "Desktop").
"""

import logging

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

log = logging.getLogger(__name__)

_KHMER_PREVIEW = "សួស្ដី ជំរាបសួរ — ការរៀបចំ បណ្ណាល័យ — LibrarianAI"
_LATIN_PREVIEW = "The quick brown fox — LibrarianAI 2025 · Category · Author"

# Cover resolution options: label → max pixel height stored in settings
_COVER_SIZES = [
    ("Small  (96 px)", 96),
    ("Medium (140 px)", 140),
    ("Large  (178 px)", 178),
]


def load_settings() -> dict:
    """Return all persisted settings as a dict."""
    s = QSettings("LibrarianAI", "Desktop")
    return {
        # Application window behaviour
        "remember_geometry": s.value("app/remember_geometry", True,   type=bool),
        "remember_view":     s.value("app/remember_view",     True,   type=bool),
        "default_view":      s.value("app/default_view",      "list", type=str),
        # Document information display
        "doc_font_size":     s.value("doc/font_size",         10,     type=int),
        "khmer_font":        s.value("doc/khmer_font",        "",     type=str),
        "cover_height":      s.value("doc/cover_height",      178,    type=int),
    }


def apply_settings_to_app(app) -> None:
    """
    Apply persisted settings to the running QApplication.

    The app-wide UI font is owned by the dark theme (Segoe UI 10 pt).
    This function intentionally does NOT change the app font — document info
    font size is applied per-widget by MainWindow after this call.
    """
    # Nothing to apply at the app level for now; hook kept for future use.
    _ = app  # noqa: F841


class SettingsDialog(QDialog):
    """
    Two-section settings dialog.

    Emits ``settings_changed`` when the user applies changes so MainWindow
    can refresh the table delegate and card view.
    """

    settings_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(540)
        self.setModal(True)
        self.resize(580, 520)
        self._s = QSettings("LibrarianAI", "Desktop")
        self._build_ui()
        self._load_current()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(14)
        outer.setContentsMargins(18, 16, 18, 14)

        outer.addWidget(self._build_app_section())
        outer.addWidget(self._build_doc_section())
        outer.addStretch()
        outer.addLayout(self._build_buttons())

    # ── Section 1: Application Window ────────────────────────────────

    def _build_app_section(self) -> QGroupBox:
        box = QGroupBox("Application Window Settings")
        form = QFormLayout(box)
        form.setSpacing(10)

        # Remember window size & position
        self._chk_geometry = QCheckBox("Remember window size and position between sessions")
        form.addRow(self._chk_geometry)

        # Remember last active view
        self._chk_remember_view = QCheckBox(
            "Remember last active view (List / Cards / Shelves) between sessions"
        )
        self._chk_remember_view.toggled.connect(self._on_remember_view_toggled)
        form.addRow(self._chk_remember_view)

        # Default startup view (enabled only when "remember view" is off)
        self._default_view_combo = QComboBox()
        self._default_view_combo.addItem("List",   "list")
        self._default_view_combo.addItem("Cards",  "cards")
        self._default_view_combo.addItem("Shelves","shelves")
        default_row = QHBoxLayout()
        default_row.addWidget(QLabel("Default startup view:"))
        default_row.addWidget(self._default_view_combo)
        default_row.addStretch()
        form.addRow(default_row)

        return box

    def _on_remember_view_toggled(self, checked: bool) -> None:
        """Grey out the default-view picker when 'remember last view' is on."""
        self._default_view_combo.setEnabled(not checked)

    # ── Section 2: Document Information ──────────────────────────────

    def _build_doc_section(self) -> QGroupBox:
        box = QGroupBox("Document Information Settings")
        form = QFormLayout(box)
        form.setSpacing(10)

        # ── Khmer font ────────────────────────────────────────────────
        self._khmer_combo = QComboBox()
        self._khmer_combo.setEditable(True)
        self._khmer_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._khmer_combo.addItem("(Same as design font — Segoe UI)", "")
        for fam in QFontDatabase.families():
            self._khmer_combo.addItem(fam, fam)
        self._khmer_combo.currentIndexChanged.connect(self._update_preview)
        form.addRow("Khmer font:", self._khmer_combo)

        khmer_note = QLabel(
            "<small>Applies <b>only</b> to title, author and filename text that "
            "contains Khmer characters (ក–អ).  All other text uses the design font.</small>"
        )
        khmer_note.setWordWrap(True)
        khmer_note.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", khmer_note)

        # ── Document font size ────────────────────────────────────────
        size_row = QHBoxLayout()
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(8, 18)
        self._size_slider.setTickInterval(1)
        self._size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._size_slider.valueChanged.connect(self._update_preview)
        size_row.addWidget(self._size_slider)
        self._size_label = QLabel("10 pt")
        self._size_label.setFixedWidth(44)
        size_row.addWidget(self._size_label)
        form.addRow("Book info font size:", size_row)

        font_note = QLabel(
            "<small>Controls text size in the list and card views only. "
            "Menus, buttons and other UI chrome are unaffected.</small>"
        )
        font_note.setWordWrap(True)
        font_note.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", font_note)

        # ── Cover resolution ──────────────────────────────────────────
        self._cover_combo = QComboBox()
        for label, _ in _COVER_SIZES:
            self._cover_combo.addItem(label)
        form.addRow("Cover image resolution:", self._cover_combo)

        cover_note = QLabel(
            "<small>Larger resolution looks sharper in card view but uses "
            "more memory.  Takes effect after the next library scan or restart.</small>"
        )
        cover_note.setWordWrap(True)
        cover_note.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", cover_note)

        # ── Preview ───────────────────────────────────────────────────
        preview_box = QGroupBox("Preview")
        pv = QVBoxLayout(preview_box)
        self._preview_khmer = QLabel(_KHMER_PREVIEW)
        self._preview_khmer.setWordWrap(True)
        self._preview_latin = QLabel(_LATIN_PREVIEW)
        self._preview_latin.setWordWrap(True)
        pv.addWidget(self._preview_khmer)
        pv.addWidget(self._preview_latin)
        form.addRow(preview_box)

        return box

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._on_reset)
        row.addWidget(reset_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)

        ok_btn = QPushButton("Apply && Close")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_apply)
        row.addWidget(ok_btn)

        return row

    # ------------------------------------------------------------------
    # Load / save / preview
    # ------------------------------------------------------------------

    def _load_current(self) -> None:
        cfg = load_settings()

        # Application window
        self._chk_geometry.setChecked(cfg["remember_geometry"])
        self._chk_remember_view.setChecked(cfg["remember_view"])
        idx = self._default_view_combo.findData(cfg["default_view"])
        self._default_view_combo.setCurrentIndex(max(0, idx))
        self._default_view_combo.setEnabled(not cfg["remember_view"])

        # Khmer font
        idx = self._khmer_combo.findData(cfg["khmer_font"])
        self._khmer_combo.setCurrentIndex(max(0, idx))

        # Doc font size
        self._size_slider.setValue(max(8, min(18, cfg["doc_font_size"])))

        # Cover resolution
        cover_h = cfg["cover_height"]
        for i, (_, h) in enumerate(_COVER_SIZES):
            if h == cover_h:
                self._cover_combo.setCurrentIndex(i)
                break

        self._update_preview()

    def _update_preview(self) -> None:
        khmer_family = self._khmer_combo.currentData() or ""
        size = self._size_slider.value()
        self._size_label.setText(f"{size} pt")

        # Khmer preview uses the chosen Khmer font
        kf = QFont(khmer_family, size) if khmer_family else QFont("Segoe UI", size)
        self._preview_khmer.setFont(kf)

        # Latin preview always uses the design font
        lf = QFont("Segoe UI", size)
        self._preview_latin.setFont(lf)

    def _on_apply(self) -> None:
        # Application window
        self._s.setValue("app/remember_geometry", self._chk_geometry.isChecked())
        self._s.setValue("app/remember_view",     self._chk_remember_view.isChecked())
        self._s.setValue("app/default_view",      self._default_view_combo.currentData())

        # Document information
        khmer_family = self._khmer_combo.currentData() or ""
        size    = self._size_slider.value()
        cover_h = _COVER_SIZES[self._cover_combo.currentIndex()][1]
        self._s.setValue("doc/font_size",    size)
        self._s.setValue("doc/khmer_font",   khmer_family)
        self._s.setValue("doc/cover_height", cover_h)

        log.info(
            "Settings saved: doc_size=%d khmer_font=%r cover_height=%d "
            "remember_geometry=%s remember_view=%s default_view=%r",
            size, khmer_family, cover_h,
            self._chk_geometry.isChecked(),
            self._chk_remember_view.isChecked(),
            self._default_view_combo.currentData(),
        )
        self.settings_changed.emit()
        self.accept()

    def _on_reset(self) -> None:
        # Application window defaults
        self._chk_geometry.setChecked(True)
        self._chk_remember_view.setChecked(True)
        self._default_view_combo.setCurrentIndex(0)   # List
        self._default_view_combo.setEnabled(False)

        # Document defaults
        self._khmer_combo.setCurrentIndex(0)
        self._size_slider.setValue(10)
        for i, (_, h) in enumerate(_COVER_SIZES):
            if h == 178:
                self._cover_combo.setCurrentIndex(i)
                break
        self._update_preview()
