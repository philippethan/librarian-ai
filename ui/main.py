"""
ui/main.py — LibrarianAI desktop application entry point.

Run with:
    python -m ui.main
or:
    python ui/main.py
"""

import logging
import os
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import QEventLoop
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _install_exception_hook(app: QApplication) -> None:
    """Show an error dialog for uncaught exceptions instead of silently crashing."""

    def handle(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        log.critical(
            "Uncaught exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        box = QMessageBox()
        box.setWindowTitle("Unexpected Error")
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(f"<b>{exc_type.__name__}</b>: {exc_value}")
        box.setDetailedText(detail)
        box.exec()

    sys.excepthook = handle


def _set_app_icon(app: QApplication) -> QIcon:
    """Load icon from ui/assets/icon.png (or .ico) and apply to the app."""
    here = Path(__file__).parent
    for name in ("icon.ico", "icon.png"):
        icon_path = here / "assets" / name
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            app.setWindowIcon(icon)
            log.debug("App icon loaded from %s", icon_path)
            return icon
    log.warning("No icon file found in ui/assets/")
    return QIcon()


def _reset_stuck_books(db_path: str) -> None:
    """Reset books stuck at 'processing' back to 'pending'.

    The FastAPI background_processor sets status='processing' before calling
    Ollama.  If the backend crashes mid-run those rows are never updated.
    The desktop app must do this reset itself since it doesn't call init_db().
    """
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "UPDATE books SET status='pending' WHERE status='processing'"
        )
        conn.commit()
        if cur.rowcount:
            log.info("Reset %d stuck 'processing' book(s) back to 'pending'", cur.rowcount)
        conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not reset stuck books: %s", exc)


def main() -> int:
    _configure_logging()
    log.info("Starting LibrarianAI desktop")

    app = QApplication(sys.argv)
    app.setApplicationName("LibrarianAI")
    app.setOrganizationName("LibrarianAI")
    app.setApplicationVersion("1.0.0")

    _set_app_icon(app)
    _install_exception_hook(app)

    # ── Dark theme (applied before any widget is created) ──────────────
    from ui.theme import apply_dark_theme  # noqa: PLC0415
    apply_dark_theme(app)

    # ── Splash screen ──────────────────────────────────────────────────
    from ui.splash_screen import SplashScreen  # noqa: PLC0415
    splash = SplashScreen()
    splash.show()
    app.processEvents()

    # ── Initialisation steps (each updates the splash) ─────────────────
    splash.set_status("Applying display settings…", progress=15)
    from ui.dialogs.settings_dialog import apply_settings_to_app  # noqa: PLC0415
    apply_settings_to_app(app)

    splash.set_status("Checking database…", progress=35)
    db_path = os.environ.get("DB_PATH", "backend/data/librarian.db")
    _reset_stuck_books(db_path)

    splash.set_status("Loading interface…", progress=55)
    from ui.main_window import MainWindow  # noqa: PLC0415

    splash.set_status("Opening library…", progress=80)
    window = MainWindow()

    # Loading done — show "click to continue" prompt and wait for the user
    splash.set_ready()

    wait = QEventLoop()
    splash.clicked.connect(wait.quit)
    wait.exec()   # blocks here until the user clicks the splash

    window.show()
    splash.finish()

    log.info("Main window open — entering event loop")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
