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
        bg     = QColor("#ddeeff") if selected else QColor("#ffffff")
        border = QColor("#1a6faf") if selected else QColor("#d0d7de")
        shadow = QColor(0, 0, 0, 20)

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
            # Placeholder — soft grey with first letter of title
            painter.fillRect(img_rect, QColor("#eef1f4"))
            painter.setPen(QColor("#b0b8c4"))
            painter.drawRect(img_rect.adjusted(0, 0, -1, -1))
            if data and data.title:
                big = QFont(painter.font())
                big.setPointSize(36)
                big.setBold(True)
                painter.setFont(big)
                painter.setPen(QColor("#c8d2dc"))
                painter.drawText(img_rect, Qt.AlignmentFlag.AlignCenter, data.title[0].upper())

        # ── Text area ────────────────────────────────────────────────────
        tx = int(card.x()) + _PAD
        ty = int(card.y()) + _PAD + _IH + _PAD
        tw = _CW - _PAD * 2
        available_h = int(card.bottom()) - ty - _PAD

        if not data:
            painter.restore()
            return

        base_font  = option.font
        small_size = max(7, base_font.pointSize() - 1)

        # Title (bold, up to 2 lines)
        title_font = QFont(base_font)
        title_font.setBold(True)
        title_font.setPointSize(small_size)
        painter.setFont(title_font)
        painter.setPen(QColor("#1a1a2e"))
        title_rect = QRect(tx, ty, tw, 38)
        title_text = data.title or data.filename or "—"
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            title_text,
        )

        # Author
        normal_font = QFont(base_font)
        normal_font.setPointSize(small_size - 1)
        painter.setFont(normal_font)
        painter.setPen(QColor("#555"))
        auth_rect = QRect(tx, ty + 42, tw, 16)
        author_text = data.author or ""
        fm = painter.fontMetrics()
        painter.drawText(auth_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                         fm.elidedText(author_text, Qt.TextElideMode.ElideRight, tw))

        # Year
        painter.setPen(QColor("#888"))
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
        badge_font = QFont(normal_font)
        badge_font.setPointSize(max(6, small_size - 2))
        painter.setFont(badge_font)
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

    def __init__(self, covers_dir: str, parent=None) -> None:
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

        self.setItemDelegate(_CardDelegate(self))
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu)

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
            # disk check so switching to card view while the loader is still
            # running still shows covers that are on disk.
            if not QPixmapCache.find(cover_key):
                path = os.path.join(self._covers_dir, f"{book.id}.jpg")
                if os.path.exists(path):
                    pm = QPixmap(path)
                    if not pm.isNull():
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
