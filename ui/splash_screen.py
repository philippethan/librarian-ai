"""
ui/splash_screen.py — Animated startup splash screen for LibrarianAI.

Design: frameless dark card, custom-painted with QPainter.
  • Deep navy gradient background
  • Stylised open-book icon (left)
  • App name + tagline (right)
  • Thin progress bar with a moving shimmer
  • Status text + version number

Usage (from main.py):
    splash = SplashScreen()
    splash.show()
    splash.set_status("Loading…", progress=20)
    …
    splash.finish(main_window)   # fade out, then close
"""

import math

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QWidget

# ── Dimensions ─────────────────────────────────────────────────────────────
_W   = 560   # total widget width  (includes shadow margin)
_H   = 300   # total widget height
_SH  = 16    # shadow margin on each side
_RAD = 14    # card corner radius

# ── Palette ────────────────────────────────────────────────────────────────
_BG_TOP    = QColor("#1a2438")
_BG_BTN    = QColor("#0d1520")
_ACCENT    = QColor("#4a8fff")
_ACCENT2   = QColor("#7b55ef")
_BORDER    = QColor("#253045")
_TEXT_H    = QColor("#dce6f0")    # heading
_TEXT_SUB  = QColor("#5d7a96")    # subheading / status
_TEXT_VER  = QColor("#2d3d4e")    # version (dim)
_BAR_BG    = QColor("#1b2a3a")
_BOOK_L    = QColor("#2a4a80")    # left page fill
_BOOK_R    = QColor("#1c3660")    # right page fill
_BOOK_LINE = QColor("#4a8fff")    # page accent lines


class SplashScreen(QWidget):
    """Frameless, translucent startup splash screen.

    Stays visible after loading completes until the user clicks anywhere.
    Emits ``clicked`` at that point so the caller can show the main window.
    """

    VERSION = "1.0.0"
    clicked = pyqtSignal()   # emitted when user clicks to dismiss

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(_W, _H)
        self._center_on_screen()

        self._status   = "Starting…"
        self._progress = 0       # 0–100
        self._shimmer  = 0       # moving highlight offset
        self._pulse    = 0       # frame counter for "click to continue" pulse
        self._ready    = False   # True once loading is complete

        # ~40 fps shimmer + pulse tick
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start(25)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_status(self, text: str, progress: int = -1) -> None:
        """Update status label and optional progress (0-100)."""
        self._status = text
        if 0 <= progress <= 100:
            self._progress = progress
        self.update()
        QApplication.processEvents()

    def set_ready(self) -> None:
        """Call after all loading steps — shows the 'click to continue' prompt."""
        self._progress = 100
        self._status   = "Ready!"
        self._ready    = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()
        QApplication.processEvents()

    def finish(self, after_widget: QWidget | None = None) -> None:
        """Fade out and close. *after_widget* is ignored (kept for API compat)."""
        self._ticker.stop()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(400)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self.close)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._ready:
            self.clicked.emit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width()  - _W) // 2,
            (screen.height() - _H) // 2,
        )

    def _tick(self) -> None:
        self._shimmer = (self._shimmer + 4) % 300
        self._pulse   = (self._pulse + 1) % 360
        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        card = QRectF(_SH, _SH, _W - _SH * 2, _H - _SH * 2)

        self._draw_shadow(p, card)
        self._draw_card(p, card)
        self._draw_top_accent(p, card)
        self._draw_book_icon(p, card)
        self._draw_text(p, card)
        self._draw_progress_bar(p, card)
        p.end()

    # ── Shadow ────────────────────────────────────────────────────────

    def _draw_shadow(self, p: QPainter, card: QRectF) -> None:
        for i in range(_SH, 0, -1):
            alpha = int(60 * (1 - i / _SH) ** 2)
            shadow = card.adjusted(-i * 0.4, -i * 0.4, i * 0.4, i * 0.6)
            path = QPainterPath()
            path.addRoundedRect(shadow, _RAD + i * 0.3, _RAD + i * 0.3)
            p.fillPath(path, QColor(0, 0, 0, alpha))

    # ── Card background ───────────────────────────────────────────────

    def _draw_card(self, p: QPainter, card: QRectF) -> None:
        path = QPainterPath()
        path.addRoundedRect(card, _RAD, _RAD)

        grad = QLinearGradient(card.left(), card.top(), card.left(), card.bottom())
        grad.setColorAt(0.0, _BG_TOP)
        grad.setColorAt(1.0, _BG_BTN)
        p.fillPath(path, grad)

        p.setPen(QPen(_BORDER, 1.0))
        p.drawPath(path)

    # ── Top accent stripe ─────────────────────────────────────────────

    def _draw_top_accent(self, p: QPainter, card: QRectF) -> None:
        stripe_w = card.width() * 0.55
        stripe_x = card.left() + (card.width() - stripe_w) / 2
        stripe_y = card.top() + 1

        grad = QLinearGradient(stripe_x, 0, stripe_x + stripe_w, 0)
        grad.setColorAt(0.0, QColor(74, 143, 255, 0))
        grad.setColorAt(0.3, _ACCENT)
        grad.setColorAt(0.7, _ACCENT2)
        grad.setColorAt(1.0, QColor(123, 85, 239, 0))

        p.setPen(QPen(QBrush(grad), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(stripe_x), int(stripe_y), int(stripe_x + stripe_w), int(stripe_y))

    # ── Book icon (painted, no image file needed) ─────────────────────

    def _draw_book_icon(self, p: QPainter, card: QRectF) -> None:
        bx = int(card.left()) + 44
        by = int(card.top())  + 52
        bw = 64
        bh = 84
        mid = bx + bw // 2

        p.save()

        # Soft glow behind the book
        glow = QRadialGradient(bx + bw // 2, by + bh // 2, bw)  # noqa: F841
        glow_path = QPainterPath()
        glow_path.addEllipse(bx - 8, by - 8, bw + 16, bh + 16)
        p.fillPath(glow_path, QColor(74, 143, 255, 18))

        # Left page
        lp = QPainterPath()
        lp.moveTo(mid, by + 10)
        lp.cubicTo(mid - 6, by + 4, bx + 6, by - 2, bx, by)
        lp.lineTo(bx + 2, by + bh)
        lp.lineTo(mid, by + bh - 6)
        lp.closeSubpath()
        p.fillPath(lp, _BOOK_L)
        p.setPen(QPen(_BOOK_LINE, 0.8))
        p.drawPath(lp)

        # Right page
        rp = QPainterPath()
        rp.moveTo(mid, by + 10)
        rp.cubicTo(mid + 6, by + 4, bx + bw - 6, by - 2, bx + bw, by)
        rp.lineTo(bx + bw - 2, by + bh)
        rp.lineTo(mid, by + bh - 6)
        rp.closeSubpath()
        p.fillPath(rp, _BOOK_R)
        p.setPen(QPen(_BOOK_LINE, 0.8))
        p.drawPath(rp)

        # Spine
        spine_grad = QLinearGradient(mid - 1, by, mid + 1, by)
        spine_grad.setColorAt(0, _ACCENT)
        spine_grad.setColorAt(1, _ACCENT2)
        p.setPen(QPen(QBrush(spine_grad), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(mid, by + 10, mid, by + bh - 6)

        # Text lines on left page
        p.setPen(QPen(QColor(74, 143, 255, 90), 1))
        for i in range(4):
            ly = by + 22 + i * 14
            x0 = bx + 8
            x1 = mid - 8 - (6 if i % 2 else 0)
            p.drawLine(x0, ly, x1, ly)

        # Text lines on right page
        for i in range(4):
            ly = by + 22 + i * 14
            x0 = mid + 8
            x1 = bx + bw - 8 - (6 if i % 2 == 0 else 0)
            p.drawLine(x0, ly, x1, ly)

        p.restore()

    # ── App name + tagline ────────────────────────────────────────────

    def _draw_text(self, p: QPainter, card: QRectF) -> None:
        text_x = int(card.left()) + 128
        text_w = int(card.width()) - 128 - 24

        # App name
        f = QFont("Segoe UI", 26, QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(_TEXT_H)
        p.drawText(
            QRect(text_x, int(card.top()) + 48, text_w, 42),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "LibrarianAI",
        )

        # Tagline
        f2 = QFont("Segoe UI", 10)
        p.setFont(f2)
        p.setPen(_TEXT_SUB)
        p.drawText(
            QRect(text_x, int(card.top()) + 94, text_w, 22),
            Qt.AlignmentFlag.AlignLeft,
            "Your AI-powered personal library",
        )

        # Thin divider
        div_y = int(card.top()) + 125
        p.setPen(QPen(_BORDER, 1))
        p.drawLine(text_x, div_y, int(card.right()) - 24, div_y)

    # ── Progress bar + status ─────────────────────────────────────────

    def _draw_progress_bar(self, p: QPainter, card: QRectF) -> None:
        bar_x = int(card.left()) + 44
        bar_y = int(card.bottom()) - 54
        bar_w = int(card.width()) - 88
        bar_h = 5
        r     = bar_h // 2

        p.save()

        # Track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_BAR_BG)
        p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, r, r)

        # Fill
        fill_w = max(0, int(bar_w * self._progress / 100))
        if fill_w > 0:
            fill_grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
            fill_grad.setColorAt(0.0, _ACCENT)
            fill_grad.setColorAt(1.0, _ACCENT2)

            clip = QPainterPath()
            clip.addRoundedRect(bar_x, bar_y, fill_w, bar_h, r, r)
            p.setClipPath(clip)
            p.fillPath(clip, fill_grad)

            # Shimmer
            sx = bar_x + (self._shimmer % (bar_w + 60)) - 30
            if bar_x - 30 < sx < bar_x + fill_w + 30:
                sh_grad = QLinearGradient(sx, 0, sx + 40, 0)
                sh_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
                sh_grad.setColorAt(0.5, QColor(255, 255, 255, 55))
                sh_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.setBrush(QBrush(sh_grad))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(sx, bar_y, 40, bar_h)

        p.restore()

        # Status text
        f = QFont("Segoe UI", 9)
        p.setFont(f)
        p.setPen(_TEXT_SUB)
        p.drawText(
            QRect(bar_x, bar_y + bar_h + 6, bar_w - 70, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._status,
        )

        # Version (right-aligned)
        fv = QFont("Segoe UI", 8)
        p.setFont(fv)
        p.setPen(_TEXT_VER)
        p.drawText(
            QRect(bar_x, bar_y + bar_h + 6, bar_w, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"v{self.VERSION}",
        )

        # "Click to continue" prompt — only shown when ready, pulses gently
        if self._ready:
            # alpha oscillates between 80 and 220 using a sine wave
            alpha = int(150 + 70 * math.sin(math.radians(self._pulse * 2)))
            color = QColor(74, 143, 255, alpha)
            fc = QFont("Segoe UI", 9)
            fc.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
            p.setFont(fc)
            p.setPen(color)
            p.drawText(
                QRect(bar_x, bar_y + bar_h + 26, bar_w, 18),
                Qt.AlignmentFlag.AlignCenter,
                "▶  Click anywhere to open your library",
            )


