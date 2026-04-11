"""
ui/dialogs/settings_dialog.py — Application settings dialog.

Currently provides:
  • Display font — choose any installed font family (important for Khmer script
    support: select a Khmer-capable font like "Khmer OS", "Noto Sans Khmer",
    "Khmer UI", etc. so that Khmer text in titles/descriptions renders correctly)
  • Font size — coarse zoom (8 pt … 18 pt)

Settings are persisted via QSettings("LibrarianAI", "Desktop") which writes
to the Windows registry under HKCU/Software/LibrarianAI/Desktop.
"""

import logging

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
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

# Khmer sample text for the preview
_KHMER_PREVIEW = "សួស្ដី ជំរាបសួរ — ห้องสมุด — LibrarianAI"
_LATIN_PREVIEW = "The quick brown fox jumps over the lazy dog — LibrarianAI 1234"


def load_settings() -> dict:
    """Return the persisted display settings (font family + size)."""
    s = QSettings("LibrarianAI", "Desktop")
    return {
        "font_family": s.value("display/font_family", "", type=str),
        "font_size":   s.value("display/font_size",   10,  type=int),
    }


def apply_settings_to_app(app) -> None:
    """Read settings and apply the chosen font to QApplication."""
    cfg = load_settings()
    family = cfg["font_family"]
    size   = max(8, min(18, cfg["font_size"]))
    if family:
        app.setFont(QFont(family, size))
    else:
        f = app.font()
        f.setPointSize(size)
        app.setFont(f)


class SettingsDialog(QDialog):
    """
    Usage::

        dlg = SettingsDialog(parent=self)
        dlg.settings_changed.connect(app.instance(), apply_settings_to_app)
        dlg.exec()
    """

    settings_changed = pyqtSignal()   # emitted when Apply / OK is clicked

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        self.setModal(True)
        self.resize(540, 380)

        self._s = QSettings("LibrarianAI", "Desktop")
        self._build_ui()
        self._load_current()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(12)
        outer.setContentsMargins(16, 14, 16, 12)

        # ── Font section ──────────────────────────────────────────────
        font_box = QGroupBox("Display Font")
        form = QFormLayout(font_box)
        form.setSpacing(8)

        # Family picker
        self._family_combo = QComboBox()
        self._family_combo.setEditable(True)
        self._family_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._family_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)

        # Populate with all installed font families
        all_families = QFontDatabase.families()
        self._family_combo.addItem("(Application default)", "")
        for fam in all_families:
            self._family_combo.addItem(fam, fam)

        self._family_combo.currentIndexChanged.connect(self._update_preview)
        form.addRow("Font family:", self._family_combo)

        # Helpful note for Khmer users
        khmer_note = QLabel(
            "<small>For Khmer script, choose a font that supports it: "
            "<b>Khmer OS</b>, <b>Khmer UI</b>, <b>Noto Sans Khmer</b>, "
            "<b>Kh Muol</b>, etc.  The preview below shows how the selected "
            "font renders both Khmer and Latin text.</small>"
        )
        khmer_note.setWordWrap(True)
        khmer_note.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", khmer_note)

        # Font size slider
        size_row = QHBoxLayout()
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(8, 18)
        self._size_slider.setTickInterval(1)
        self._size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._size_slider.valueChanged.connect(self._update_preview)
        size_row.addWidget(self._size_slider)
        self._size_label = QLabel("10 pt")
        self._size_label.setFixedWidth(40)
        size_row.addWidget(self._size_label)
        form.addRow("Font size:", size_row)

        outer.addWidget(font_box)

        # ── Preview ───────────────────────────────────────────────────
        preview_box = QGroupBox("Preview")
        pv_layout = QVBoxLayout(preview_box)
        self._preview_khmer = QLabel(_KHMER_PREVIEW)
        self._preview_khmer.setWordWrap(True)
        self._preview_latin = QLabel(_LATIN_PREVIEW)
        self._preview_latin.setWordWrap(True)
        pv_layout.addWidget(self._preview_khmer)
        pv_layout.addWidget(self._preview_latin)
        outer.addWidget(preview_box)

        outer.addStretch()

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton("Reset to Default")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Apply & Close")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(ok_btn)

        outer.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_current(self) -> None:
        family = self._s.value("display/font_family", "", type=str)
        size   = self._s.value("display/font_size",   10,  type=int)

        # Select current family in combo
        idx = self._family_combo.findData(family)
        self._family_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self._size_slider.setValue(max(8, min(18, size)))
        self._update_preview()

    def _update_preview(self) -> None:
        family = self._family_combo.currentData() or ""
        size   = self._size_slider.value()
        self._size_label.setText(f"{size} pt")

        f = QFont(family, size) if family else QFont()
        f.setPointSize(size)
        self._preview_khmer.setFont(f)
        self._preview_latin.setFont(f)

    def _on_apply(self) -> None:
        family = self._family_combo.currentData() or ""
        size   = self._size_slider.value()
        self._s.setValue("display/font_family", family)
        self._s.setValue("display/font_size",   size)
        log.info("Settings saved: font=%r size=%d", family, size)
        self.settings_changed.emit()
        self.accept()

    def _on_reset(self) -> None:
        self._family_combo.setCurrentIndex(0)   # "(Application default)"
        self._size_slider.setValue(10)
        self._update_preview()
