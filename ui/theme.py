"""
ui/theme.py — Dark theme for LibrarianAI.

Call apply_dark_theme(app) once after QApplication is created.

Python code that paints directly with QPainter should import the COLORS
dict so all colours stay in sync with the stylesheet.
"""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Colour palette — single source of truth
# ---------------------------------------------------------------------------

COLORS: dict[str, str] = {
    # Backgrounds (darkest → lightest)
    "bg_deep":       "#0d1520",   # window / table background
    "bg_base":       "#111d2b",   # widget default bg
    "bg_raised":     "#1a2438",   # cards, inputs, slightly raised surfaces
    "bg_hover":      "#1e2d40",   # hover state
    "bg_selected":   "#1e3a5f",   # selected / active row

    # Borders
    "border":        "#253045",   # standard border
    "border_focus":  "#4a8fff",   # focused input / selected card

    # Accent
    "accent":        "#4a8fff",   # primary blue
    "accent2":       "#7b55ef",   # secondary purple (gradients)

    # Text
    "text_primary":  "#dce6f0",   # normal text
    "text_secondary":"#8899aa",   # muted / author / secondary labels
    "text_dim":      "#5d7a96",   # very muted / status / year
    "text_disabled": "#2d3d4e",   # disabled / version watermark

    # Semantic
    "danger":        "#c0392b",   # delete / error actions
    "danger_bg":     "#2d1515",   # duplicate-row tint
    "success":       "#27ae60",   # valid / done badge
    "warning":       "#f39c12",   # partial badge
}


def _c(key: str) -> str:
    """Return the hex string for a palette key."""
    return COLORS[key]


def _qc(key: str) -> QColor:
    """Return a QColor for a palette key."""
    return QColor(COLORS[key])


# ---------------------------------------------------------------------------
# Qt stylesheet
# ---------------------------------------------------------------------------

_QSS = f"""
/* ── Global ───────────────────────────────────────────────────────── */
QWidget {{
    background-color: {_c("bg_base")};
    color: {_c("text_primary")};
    font-family: "Segoe UI";
    font-size: 10pt;
    border: none;
    selection-background-color: {_c("bg_selected")};
    selection-color: {_c("text_primary")};
}}
QMainWindow, QDialog {{
    background-color: {_c("bg_deep")};
}}

/* ── Menu bar ─────────────────────────────────────────────────────── */
QMenuBar {{
    background-color: {_c("bg_base")};
    border-bottom: 1px solid {_c("border")};
    padding: 2px;
}}
QMenuBar::item {{
    padding: 4px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background-color: {_c("bg_hover")};
}}
QMenu {{
    background-color: {_c("bg_raised")};
    border: 1px solid {_c("border")};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {_c("bg_selected")};
    color: {_c("accent")};
}}
QMenu::separator {{
    height: 1px;
    background: {_c("border")};
    margin: 4px 8px;
}}

/* ── Toolbar ──────────────────────────────────────────────────────── */
QToolBar {{
    background-color: {_c("bg_base")};
    border-bottom: 1px solid {_c("border")};
    spacing: 4px;
    padding: 4px 8px;
}}

/* ── Status bar ───────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {_c("bg_deep")};
    color: {_c("text_dim")};
    border-top: 1px solid {_c("border")};
}}

/* ── Buttons ──────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {_c("bg_raised")};
    color: {_c("text_primary")};
    border: 1px solid {_c("border")};
    border-radius: 5px;
    padding: 5px 14px;
    min-height: 24px;
}}
QPushButton:hover {{
    background-color: {_c("bg_selected")};
    border-color: {_c("accent")};
}}
QPushButton:pressed {{
    background-color: {_c("bg_deep")};
}}
QPushButton:disabled {{
    color: {_c("text_disabled")};
    border-color: {_c("bg_raised")};
    background-color: {_c("bg_base")};
}}

/* ── Inputs ───────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
    background-color: {_c("bg_deep")};
    color: {_c("text_primary")};
    border: 1px solid {_c("border")};
    border-radius: 5px;
    padding: 4px 8px;
    selection-background-color: {_c("bg_selected")};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border-color: {_c("accent")};
}}
QLineEdit:read-only {{
    color: {_c("text_dim")};
    background-color: {_c("bg_base")};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {_c("bg_raised")};
    border: none;
    width: 16px;
}}
QSpinBox::up-arrow  {{ border-bottom: 4px solid {_c("text_secondary")}; border-left: 3px solid transparent; border-right: 3px solid transparent; width:0; height:0; }}
QSpinBox::down-arrow {{ border-top:  4px solid {_c("text_secondary")}; border-left: 3px solid transparent; border-right: 3px solid transparent; width:0; height:0; }}

/* ── Combo box ────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {_c("bg_deep")};
    color: {_c("text_primary")};
    border: 1px solid {_c("border")};
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 24px;
}}
QComboBox:hover {{ border-color: {_c("accent")}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {_c("text_secondary")};
    width: 0; height: 0;
}}
QComboBox QAbstractItemView {{
    background-color: {_c("bg_raised")};
    border: 1px solid {_c("border")};
    selection-background-color: {_c("bg_selected")};
    selection-color: {_c("accent")};
    outline: none;
    border-radius: 4px;
}}

/* ── Table ────────────────────────────────────────────────────────── */
QTableWidget, QTableView {{
    background-color: {_c("bg_deep")};
    alternate-background-color: {_c("bg_base")};
    color: {_c("text_primary")};
    gridline-color: {_c("bg_raised")};
    border: none;
    selection-background-color: {_c("bg_selected")};
    selection-color: {_c("text_primary")};
}}
QTableWidget::item:hover {{ background-color: {_c("bg_hover")}; }}

/* ── Header view ──────────────────────────────────────────────────── */
QHeaderView {{ background-color: {_c("bg_base")}; }}
QHeaderView::section {{
    background-color: {_c("bg_base")};
    color: {_c("text_secondary")};
    border: none;
    border-bottom: 1px solid {_c("border")};
    border-right: 1px solid {_c("bg_raised")};
    padding: 4px 8px;
    font-weight: 600;
}}
QHeaderView::section:hover {{
    background-color: {_c("bg_raised")};
    color: {_c("text_primary")};
}}
QHeaderView::section:checked {{ color: {_c("accent")}; }}
/* Corner button (top-left of table) */
QAbstractButton {{
    background-color: {_c("bg_base")};
    border: none;
    border-bottom: 1px solid {_c("border")};
    border-right: 1px solid {_c("border")};
}}

/* ── List widget ──────────────────────────────────────────────────── */
QListWidget {{
    background-color: {_c("bg_deep")};
    alternate-background-color: {_c("bg_base")};
    color: {_c("text_primary")};
    border: none;
    outline: none;
}}
QListWidget::item:selected {{
    background-color: {_c("bg_selected")};
    color: {_c("text_primary")};
}}
QListWidget::item:hover {{ background-color: {_c("bg_hover")}; }}

/* ── Scrollbars ───────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {_c("border")};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {_c("bg_hover")}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px; margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {_c("border")};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {_c("bg_hover")}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── Splitter ─────────────────────────────────────────────────────── */
QSplitter::handle {{ background: {_c("border")}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical   {{ height: 1px; }}

/* ── Group box ────────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {_c("border")};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {_c("text_dim")};
    font-size: 9pt;
}}

/* ── Tabs ─────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {_c("border")};
    background-color: {_c("bg_base")};
}}
QTabBar::tab {{
    background-color: {_c("bg_deep")};
    color: {_c("text_secondary")};
    border: 1px solid {_c("border")};
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    padding: 6px 16px;
}}
QTabBar::tab:selected {{
    background-color: {_c("bg_base")};
    color: {_c("accent")};
}}
QTabBar::tab:hover {{ color: {_c("text_primary")}; }}

/* ── Check box ────────────────────────────────────────────────────── */
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {_c("border")};
    border-radius: 3px;
    background: {_c("bg_deep")};
}}
QCheckBox::indicator:checked {{
    background-color: {_c("accent")};
    border-color: {_c("accent")};
    image: none;
}}
QCheckBox::indicator:hover {{ border-color: {_c("accent")}; }}

/* ── Progress bar ─────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {_c("bg_raised")};
    border: none;
    border-radius: 3px;
    height: 6px;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {_c("accent")}, stop:1 {_c("accent2")});
    border-radius: 3px;
}}

/* ── Labels ───────────────────────────────────────────────────────── */
QLabel {{ background: transparent; }}

/* ── Tooltip ──────────────────────────────────────────────────────── */
QToolTip {{
    background-color: {_c("bg_raised")};
    color: {_c("text_primary")};
    border: 1px solid {_c("border")};
    border-radius: 4px;
    padding: 4px 8px;
}}

/* ── Message box ──────────────────────────────────────────────────── */
QMessageBox {{ background-color: {_c("bg_base")}; }}
QMessageBox QLabel {{ color: {_c("text_primary")}; }}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Apply the dark stylesheet and palette to the application."""
    app.setStyleSheet(_QSS)

    # Also set the QPalette so native painting (focus rings, etc.) matches
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          _qc("bg_base"))
    pal.setColor(QPalette.ColorRole.WindowText,      _qc("text_primary"))
    pal.setColor(QPalette.ColorRole.Base,            _qc("bg_deep"))
    pal.setColor(QPalette.ColorRole.AlternateBase,   _qc("bg_base"))
    pal.setColor(QPalette.ColorRole.Text,            _qc("text_primary"))
    pal.setColor(QPalette.ColorRole.BrightText,      _qc("text_primary"))
    pal.setColor(QPalette.ColorRole.Button,          _qc("bg_raised"))
    pal.setColor(QPalette.ColorRole.ButtonText,      _qc("text_primary"))
    pal.setColor(QPalette.ColorRole.Highlight,       _qc("bg_selected"))
    pal.setColor(QPalette.ColorRole.HighlightedText, _qc("text_primary"))
    pal.setColor(QPalette.ColorRole.Link,            _qc("accent"))
    pal.setColor(QPalette.ColorRole.Mid,             _qc("text_dim"))
    pal.setColor(QPalette.ColorRole.Dark,            _qc("border"))
    pal.setColor(QPalette.ColorRole.Shadow,          _qc("bg_deep"))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     _qc("bg_raised"))
    pal.setColor(QPalette.ColorRole.ToolTipText,     _qc("text_primary"))
    app.setPalette(pal)
