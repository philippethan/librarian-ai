"""
ui/views/card_view.py — Grid/card view for the book library.

Each card shows cover thumbnail, title (bold), author, year, and a
status badge.  Double-click opens BookDetailDialog; right-click shows
a context menu.  Cover images are loaded synchronously from the local
covers directory (backend/data/covers/{id}.jpg).
"""

import os
from typing import NamedTuple

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from ui.theme import COLORS as _T

# Shared Khmer detection (same logic as in main_window)
def _has_khmer(text: str) -> bool:
    return any("\u1780" <= ch <= "\u17ff" for ch in (text or ""))
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPixmapCache,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
)

# ── Card geometry ──────────────────────────────────────────────────────────
_CW  = 190   # card width  (excluding outer spacing)
_CH  = 275   # card height (excluding outer spacing)
_IH  = 180   # cover image height
_PAD = 6     # inner padding
_RAD = 6     # corner radius
_SP  = 8     # spacing between cards

# Status badge colours
_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "done":       ("#27ae60", "#ffffff"),
    "partial":    ("#f39c12", "#ffffff"),
    "pending":    ("#95a5a6", "#ffffff"),
    "processing": ("#2980b9", "#ffffff"),
    "duplicate":  ("#8e44ad", "#ffffff"),
    "error":      ("#e74c3c", "#ffffff"),
}


class _CardData(NamedTuple):
    book_id:  int
    title:    str
    author:   str
    year:     str
    status:   str
    filename: str
    cover_key: str   # QPixmapCache key


# ---------------------------------------------------------------------------
# Custom delegate — draws each card
# ---------------------------------------------------------------------------

class _CardDelegate(QStyledItemDelegate):

    def __init__(self, doc_size: int = 10, khmer_font: str = "", parent=None) -> None:
        super().__init__(parent)
        self._doc_size   = doc_size
        self._khmer_font = khmer_font

    def update_font_settings(self, doc_size: int, khmer_font: str) -> None:
        self._doc_size   = doc_size
        self._khmer_font = khmer_font

    def _font_for(self, text: str, bold: bool = False) -> QFont:
        if self._khmer_font and _has_khmer(text):
            f = QFont(self._khmer_font, self._doc_size)
        else:
            f = QFont("Segoe UI", self._doc_size)
        f.setBold(bold)
        return f

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(_CW + _SP * 2, _CH + _SP * 2)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Outer cell rect → inset to card rect
        r = option.rect
        card = QRectF(
            r.x() + _SP, r.y() + _SP,
            _CW, _CH,
        )

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        bg     = QColor(_T["bg_selected"]) if selected else QColor(_T["bg_raised"])
        border = QColor(_T["border_focus"]) if selected else QColor(_T["border"])
        shadow = QColor(0, 0, 0, 40)

        # Drop shadow
        shadow_rect = card.translated(2, 2)
        path_sh = QPainterPath()
        path_sh.addRoundedRect(shadow_rect, _RAD, _RAD)
        painter.fillPath(path_sh, shadow)

        # Card background + border
        path = QPainterPath()
        path.addRoundedRect(card, _RAD, _RAD)
        painter.fillPath(path, bg)
        painter.setPen(QPen(border, 1.5 if selected else 1.0))
        painter.drawPath(path)

        # ── Cover image ──────────────────────────────────────────────────
        img_x = int(card.x()) + _PAD
        img_y = int(card.y()) + _PAD
        img_w = _CW - _PAD * 2
        img_rect = QRect(img_x, img_y, img_w, _IH)

        data: _CardData | None = index.data(Qt.ItemDataRole.UserRole)
        cover_pm = QPixmapCache.find(data.cover_key) if data else None

        if cover_pm and not cover_pm.isNull():
            scaled = cover_pm.scaled(
                img_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            dx = img_rect.x() + (img_rect.width()  - scaled.width())  // 2
            dy = img_rect.y() + (img_rect.height() - scaled.height()) // 2
            # Clip to rounded top
            painter.save()
            clip_path = QPainterPath()
            clip_path.addRoundedRect(QRectF(card.x(), card.y(), card.width(), _IH + _PAD + _RAD), _RAD, _RAD)
            painter.setClipPath(clip_path)
            painter.drawPixmap(dx, dy, scaled)
            painter.restore()
        else:
            # Placeholder — dark background with first letter of title
            painter.fillRect(img_rect, QColor(_T["bg_deep"]))
            painter.setPen(QColor(_T["border"]))
            painter.drawRect(img_rect.adjusted(0, 0, -1, -1))
            if data and data.title:
                big = QFont(painter.font())
                big.setPointSize(36)
                big.setBold(True)
                painter.setFont(big)
                painter.setPen(QColor(_T["text_disabled"]))
                painter.drawText(img_rect, Qt.AlignmentFlag.AlignCenter, data.title[0].upper())

        # ── Text area ────────────────────────────────────────────────────
        tx = int(card.x()) + _PAD
        ty = int(card.y()) + _PAD + _IH + _PAD
        tw = _CW - _PAD * 2
        if not data:
            painter.restore()
            return

        # Title (bold, Khmer-aware)
        title_text = data.title or data.filename or "—"
        painter.setFont(self._font_for(title_text, bold=True))
        painter.setPen(QColor(_T["text_primary"]))
        title_rect = QRect(tx, ty, tw, 38)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            title_text,
        )

        # Author (Khmer-aware)
        author_text = data.author or ""
        painter.setFont(self._font_for(author_text))
        painter.setPen(QColor(_T["text_secondary"]))
        auth_rect = QRect(tx, ty + 42, tw, 16)
        fm = painter.fontMetrics()
        painter.drawText(auth_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                         fm.elidedText(author_text, Qt.TextElideMode.ElideRight, tw))

        # Year — always design font (Latin digits)
        painter.setFont(QFont("Segoe UI", max(7, self._doc_size - 1)))
        painter.setPen(QColor(_T["text_dim"]))
        year_rect = QRect(tx, ty + 62, tw // 2, 14)
        painter.drawText(year_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                         data.year or "")

        # Status badge
        status = data.status or ""
        fg, bg_s = _STATUS_COLORS.get(status, ("#777", "#f0f0f0"))
        badge_fm = painter.fontMetrics()
        bw = badge_fm.horizontalAdvance(status) + 10
        bh = 14
        bx = int(card.right()) - _PAD - bw
        by = ty + 60
        badge_rect = QRect(bx, by, bw, bh)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(bg_s)))
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(QColor(fg))
        painter.setFont(QFont("Segoe UI", max(6, self._doc_size - 2)))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, status)

        painter.restore()


# ---------------------------------------------------------------------------
# CardView widget
# ---------------------------------------------------------------------------

class CardView(QListWidget):
    """
    Grid view showing one card per book.  Connects to MainWindow via signals:
      book_activated(book_id)     — double-click
      context_menu_requested(book_ids, global_pos) — right-click
    """

    book_activated        = pyqtSignal(int)         # book_id
    context_menu_requested = pyqtSignal(list, QPoint)  # [book_id, …], global pos

    def __init__(self, covers_dir: str, doc_size: int = 10,
                 khmer_font: str = "", parent=None) -> None:
        super().__init__(parent)
        self._covers_dir = covers_dir

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(_SP)
        self.setGridSize(QSize(_CW + _SP * 2, _CH + _SP * 2))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._delegate = _CardDelegate(doc_size, khmer_font, self)
        self.setItemDelegate(self._delegate)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def set_doc_font(self, doc_size: int, khmer_font: str) -> None:
        """Update document-info font settings for the card delegate."""
        self._delegate.update_font_settings(doc_size, khmer_font)

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def populate(self, books) -> None:
        """Replace all cards with the given list of BookRow objects.

        Covers are served from QPixmapCache (populated by _CoverLoader in the
        main window).  Any cover not yet in cache shows the placeholder; it
        will appear once the loader emits the cover and calls viewport().update().
        """
        self.clear()
        for book in books:
            cover_key = f"cover_{book.id}"

            # If this book's cover isn't in cache yet, do a quick synchronous
            # disk check so switching to card view while the async loader is
            # still running still shows covers that are already on disk.
            # Scale to card image area (178×180) before caching — inserting
            # full-resolution pixmaps would fill the 100 MB cache after ~50
            # books and silently drop all remaining covers.
            if not QPixmapCache.find(cover_key):
                path = os.path.join(self._covers_dir, f"{book.id}.jpg")
                if os.path.exists(path):
                    pm = QPixmap(path)
                    if not pm.isNull():
                        pm = pm.scaled(
                            178, 180,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        QPixmapCache.insert(cover_key, pm)

            data = _CardData(
                book_id   = book.id,
                title     = book.title,
                author    = book.author,
                year      = book.year,
                status    = book.status,
                filename  = book.filename,
                cover_key = cover_key,
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, data)
            item.setSizeHint(QSize(_CW + _SP * 2, _CH + _SP * 2))
            self.addItem(item)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        data: _CardData | None = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self.book_activated.emit(data.book_id)

    def _on_context_menu(self, pos: QPoint) -> None:
        items = self.selectedItems()
        ids   = []
        for it in items:
            d: _CardData | None = it.data(Qt.ItemDataRole.UserRole)
            if d:
                ids.append(d.book_id)
        if ids:
            self.context_menu_requested.emit(ids, self.mapToGlobal(pos))
