"""
ui/main.py — LibrarianAI desktop application entry point.

Run with:
    python -m ui.main
or:
    python ui/main.py
"""

import logging
import sys
import traceback

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


def main() -> int:
    _configure_logging()
    log.info("Starting LibrarianAI desktop")

    app = QApplication(sys.argv)
    app.setApplicationName("LibrarianAI")
    app.setOrganizationName("LibrarianAI")
    app.setApplicationVersion("1.0.0")

    _install_exception_hook(app)

    # Import here so PyQt6 is already initialised before Qt widgets are created.
    from ui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow()
    window.show()

    log.info("Main window open — entering event loop")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
