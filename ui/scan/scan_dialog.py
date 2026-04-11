"""
ui/scan/scan_dialog.py - Progress dialog for the Scan feature.

Opens modally, starts a QThread-based scan worker immediately, updates the UI
via Qt signals (thread-safe), and displays a results summary when done.
"""

import logging
import os

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

from ui.scan.scan_service import ScanService

log = logging.getLogger(__name__)

_BOOKS_PATH_DEFAULT = "C:/Users/posen/Documents/Books"


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _ScanWorker(QThread):
    """Runs ScanService.run_scan() on a background thread.

    All three signals are emitted from this thread and connected to slots on
    the main thread — PyQt6's queued-connection mechanism makes these calls
    thread-safe automatically.
    """

    progress = pyqtSignal(int, int, str)   # current, total, message
    finished = pyqtSignal(dict)            # ScanResults.as_dict()
    error    = pyqtSignal(str)             # fatal error message

    def __init__(self, service: ScanService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

    def run(self) -> None:
        try:
            results = self._service.run_scan(
                progress_callback=lambda c, t, m: self.progress.emit(c, t, m)
            )
            self.finished.emit(results)
        except Exception as exc:  # noqa: BLE001
            log.exception("ScanWorker uncaught error")
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class ScanProgressDialog(QDialog):
    """
    Modal dialog that shows live scan progress and a results summary.

    Usage::

        dlg = ScanProgressDialog(db_path, books_path, parent=self)
        if dlg.exec():          # True when user clicks Close after a scan
            self._load_books()  # Refresh table to show newly added books
    """

    def __init__(
        self,
        db_path: str,
        books_path: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self._books_path = books_path or os.environ.get("BOOKS_PATH", _BOOKS_PATH_DEFAULT)
        self._service: ScanService | None = None
        self._worker: _ScanWorker | None = None

        self._build_ui()
        self.setWindowTitle("Scan Books Directory")
        self.setMinimumWidth(560)
        self.resize(600, 340)
        self.setModal(True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # Current-file label
        self._status_label = QLabel("Preparing scan…")
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        outer.addWidget(self._status_label)

        # Progress bar — starts in indeterminate mode (max=0)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(0)
        self._progress_bar.setTextVisible(True)
        outer.addWidget(self._progress_bar)

        # Count label (e.g. "12 / 47 files")
        self._count_label = QLabel("")
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addWidget(self._count_label)

        # Errors text area — hidden until errors exist
        self._errors_box = QTextEdit()
        self._errors_box.setReadOnly(True)
        self._errors_box.setMaximumHeight(140)
        self._errors_box.setVisible(False)
        self._errors_box.setPlaceholderText("Errors will appear here.")
        outer.addWidget(self._errors_box)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.setVisible(False)
        self._close_btn.setDefault(True)
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        outer.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Start scan as soon as the dialog becomes visible.
        self._start_scan()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Ensure the worker thread stops before the dialog is destroyed."""
        if self._service is not None:
            self._service.cancel()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3_000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Scan lifecycle
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        self._service = ScanService(self._db_path, self._books_path)
        self._worker = _ScanWorker(self._service, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------
    # Slots (all called on the main thread via Qt's queued connection)
    # ------------------------------------------------------------------

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self._status_label.setText(message)

        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
            self._count_label.setText(f"{current:,} / {total:,} files")
        else:
            # Indeterminate phase (discovery / DB check)
            self._progress_bar.setMaximum(0)
            self._count_label.setText("")

    def _on_finished(self, results: dict) -> None:
        r = results

        # Snap progress to 100 %
        self._progress_bar.setMaximum(max(r["new_files"], 1))
        self._progress_bar.setValue(self._progress_bar.maximum())

        suffix = " (cancelled)" if r.get("cancelled") else ""

        lines = [
            f"Scan complete{suffix}!",
            "",
            f"Total files found:   {r['total_files']:,}",
            f"Already indexed:     {r['already_indexed']:,}",
            f"New books found:     {r['new_files']:,}",
            "",
            f"Added to library:    {r['inserted']:,}",
            f"Duplicates skipped:  {r['duplicates']:,}",
            f"Errors:              {len(r['errors']):,}",
        ]
        self._status_label.setText("\n".join(lines))
        self._count_label.setText("")

        if r["errors"]:
            displayed = r["errors"][:100]
            error_text = "\n".join(f"- {fn}: {err}" for fn, err in displayed)
            if len(r["errors"]) > 100:
                error_text += f"\n... and {len(r['errors']) - 100} more errors"
            self._errors_box.setPlainText(error_text)
            self._errors_box.setVisible(True)

        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)

    def _on_error(self, message: str) -> None:
        self._status_label.setText(f"Scan failed:\n{message}")
        self._progress_bar.setMaximum(1)
        self._progress_bar.setValue(0)
        self._count_label.setText("")

        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)

    def _on_cancel(self) -> None:
        if self._service is not None:
            self._service.cancel()
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")
        self._status_label.setText("Cancelling — finishing current file…")
