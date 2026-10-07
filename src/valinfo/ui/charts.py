"""Небольшие самодельные виджеты-графики (рисуются QPainter, цвета берут из активной темы при каждой отрисовке)."""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from valinfo.ui.theme import c, rgba
from valinfo.ui.widgets import font


def _qc(color: str, alpha: float = 1.0) -> QColor:
    q = QColor(color)
    q.setAlphaF(alpha)
    return q


class SplitBar(QWidget):
    """Горизонтальная полоса «ваша команда | противник» с разделителем по вероятности."""

    def __init__(self, p_left: float = 0.5, parent=None):
        super().__init__(parent)
        self.p = p_left
        self.lo: Optional[float] = None
        self.hi: Optional[float] = None
        self.setFixedHeight(26)

    def set_value(self, p: float, lo: Optional[float] = None, hi: Optional[float] = None) -> None:
        self.p, self.lo, self.hi = p, lo, hi
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bar = QRectF(0, 5, w, h - 10)
        split = bar.left() + bar.width() * self.p
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc(c.ally))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(split - bar.left(), 0), bar.height()), 5, 5)
        p.setBrush(_qc(c.enemy))
        p.drawRoundedRect(QRectF(split, bar.top(), max(bar.right() - split, 0), bar.height()), 5, 5)
        # скругление только снаружи: перекрываем стык прямоугольником
        p.setBrush(_qc(c.ally))
        p.drawRect(QRectF(max(split - 8, bar.left()), bar.top(), 8 if split > 8 else 0, bar.height()))
        p.setBrush(_qc(c.enemy))
        p.drawRect(QRectF(split, bar.top(), 8 if bar.right() - split > 8 else 0, bar.height()))
        if self.lo is not None and self.hi is not None:      # доверительный интервал — штрих над полосой
            x1, x2 = bar.left() + bar.width() * self.lo, bar.left() + bar.width() * self.hi
            p.setPen(QPen(_qc(c.text, 0.9), 2))
            p.drawLine(QPointF(x1, 2), QPointF(x2, 2))
            p.drawLine(QPointF(x1, 0), QPointF(x1, 5))
            p.drawLine(QPointF(x2, 0), QPointF(x2, 5))
        p.setPen(QPen(_qc(c.bg), 2))
        p.drawLine(QPointF(split, bar.top() - 2), QPointF(split, bar.bottom() + 2))
        p.end()


class HBarList(QWidget):
    """Список «подпись — полоска — значение» (распределение счёта и т.п.)."""

    ROW = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: List[Tuple[str, float, str, str]] = []   # (подпись, доля 0..1, текст значения, цвет)
        self.max_v = 1.0

    def set_rows(self, rows: Sequence[Tuple[str, float, str, str]]) -> None:
        self.rows = list(rows)
        self.max_v = max((r[1] for r in self.rows), default=1.0) or 1.0
        self.setMinimumHeight(max(len(self.rows), 1) * self.ROW + 4)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lab_w, val_w = 54, 52
        for i, (label, frac, text, color) in enumerate(self.rows):
            y = 2 + i * self.ROW
            p.setPen(_qc(c.text))
            p.setFont(font(9, True))
            p.drawText(QRectF(0, y, lab_w, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            track = QRectF(lab_w, y + 6, self.width() - lab_w - val_w - 6, self.ROW - 12)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_qc(c.border, 0.55))
            p.drawRoundedRect(track, 3, 3)
            p.setBrush(_qc(color))
            p.drawRoundedRect(QRectF(track.left(), track.top(), max(track.width() * frac / self.max_v, 2), track.height()), 3, 3)
            p.setPen(_qc(c.muted))
            p.setFont(font(9, False))
            p.drawText(QRectF(self.width() - val_w, y, val_w, self.ROW),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, text)
        p.end()


class MeterBar(QWidget):
    """Шкала 0..100 с числом (сила игрока / шанс смурфа)."""

    def __init__(self, value: float = 0.0, color: Optional[str] = None, text: str = "", parent=None):
        super().__init__(parent)
        self.value, self.color, self.text = value, color, text
        self.setFixedHeight(14)
        self.setMinimumWidth(60)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track = QRectF(0, 3, w, h - 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc(c.border, 0.7))
        p.drawRoundedRect(track, 4, 4)
        p.setBrush(_qc(self.color or c.accent))
        p.drawRoundedRect(QRectF(0, 3, max(w * min(max(self.value, 0), 100) / 100.0, 3), h - 6), 4, 4)
        p.end()


class Sparkline(QWidget):
    """Мини-график ACS по последним матчам: точки — победа/поражение."""

    def __init__(self, series: Sequence[Tuple[float, bool]] = (), parent=None):
        super().__init__(parent)
        self.series = list(series)
        self.setFixedSize(84, 28)

    def paintEvent(self, _e):
        n = len(self.series)
        if n < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        vals = [v for v, _ in self.series]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        w, h = self.width() - 6, self.height() - 8
        pts = [QPointF(3 + w * i / (n - 1), 4 + h * (1 - (v - lo) / span)) for i, (v, _) in enumerate(self.series)]
        p.setPen(QPen(_qc(c.muted, 0.8), 1.2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        p.setPen(Qt.PenStyle.NoPen)
        for pt, (_, won) in zip(pts, self.series):
            p.setBrush(_qc(c.win if won else c.loss))
            p.drawEllipse(pt, 2.3, 2.3)
        p.end()


class RadarChart(QWidget):
    """Радар по осям [(подпись, 0..1)]."""

    def __init__(self, axes: Sequence[Tuple[str, float]] = (), parent=None):
        super().__init__(parent)
        self.axes = list(axes)
        self.setMinimumSize(330, 210)

    def set_axes(self, axes) -> None:
        self.axes = list(axes)
        self.update()

    def paintEvent(self, _e):
        n = len(self.axes)
        if n < 3:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        R = min(self.width() / 2 - 112, self.height() / 2 - 24)
        ang = lambda i: -math.pi / 2 + 2 * math.pi * i / n
        pt = lambda i, k: QPointF(cx + R * k * math.cos(ang(i)), cy + R * k * math.sin(ang(i)))
        p.setPen(QPen(_qc(c.border), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in (0.33, 0.66, 1.0):
            p.drawPolygon(QPolygonF([pt(i, k) for i in range(n)]))
        for i in range(n):
            p.drawLine(QPointF(cx, cy), pt(i, 1.0))
        poly = QPolygonF([pt(i, max(v, 0.04)) for i, (_, v) in enumerate(self.axes)])
        p.setPen(QPen(_qc(c.accent), 2))
        p.setBrush(_qc(c.accent, 0.28))
        p.drawPolygon(poly)
        p.setFont(font(8, True))
        p.setPen(_qc(c.muted))
        for i, (label, _) in enumerate(self.axes):
            q = pt(i, 1.0)
            dx = math.cos(ang(i))
            w = 104
            x = q.x() + (8 if dx > 0.3 else -w - 8 if dx < -0.3 else -w / 2)
            y = q.y() + (-18 if math.sin(ang(i)) < -0.5 else 4 if math.sin(ang(i)) > 0.5 else -8)
            al = Qt.AlignmentFlag.AlignLeft if dx > 0.3 else Qt.AlignmentFlag.AlignRight if dx < -0.3 else Qt.AlignmentFlag.AlignHCenter
            p.drawText(QRectF(x, y, w, 16), al | Qt.AlignmentFlag.AlignVCenter, label)
        p.end()


class EmptyState(QWidget):
    """Заглушка по центру: значок-«радар», заголовок и пояснение."""

    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(6)
        self._icon = _RadarIcon()
        self.lbl_title = QLabel(title)
        self.lbl_title.setFont(font(15, True))
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sub = QLabel(subtitle)
        self.lbl_sub.setFont(font(10, False))
        self.lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setMaximumWidth(520)
        lay.addStretch(2)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(8)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_sub, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(3)
        self.restyle()

    def set_texts(self, title: str, subtitle: str) -> None:
        self.lbl_title.setText(title)
        self.lbl_sub.setText(subtitle)

    def restyle(self) -> None:
        self.lbl_title.setStyleSheet(f"color: {c.title};")
        self.lbl_sub.setStyleSheet(f"color: {c.muted};")
        self._icon.update()


class _RadarIcon(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(96, 96)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ctr = QPointF(48, 48)
        for r, a in ((44, 0.35), (31, 0.5), (18, 0.7)):
            p.setPen(QPen(_qc(c.accent, a), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ctr, r, r)
        p.setPen(QPen(_qc(c.accent, 0.8), 2))
        p.drawLine(ctr, QPointF(48 + 30, 48 - 30))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc(c.accent))
        p.drawEllipse(ctr, 4, 4)
        p.setBrush(_qc(c.enemy))
        p.drawEllipse(QPointF(70, 30), 3.5, 3.5)
        p.end()


class NavBar(QFrame):
    """Полоса вкладок. Вкладки — данные: `add_tab(key, text)`; позже можно добавлять новые одной строкой."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavBar")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 0, 4, 0)
        self._lay.setSpacing(2)
        self._buttons: dict = {}
        self._handler = None

    def on_select(self, handler) -> None:
        self._handler = handler

    def add_tab(self, key: str, text: str) -> None:
        b = QPushButton(text)
        b.setObjectName("NavTab")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False, k=key: self.select(k))
        self._lay.addWidget(b)
        self._buttons[key] = b

    def finish(self) -> None:
        self._lay.addStretch()

    def set_text(self, key: str, text: str) -> None:
        self._buttons[key].setText(text)

    def select(self, key: str, notify: bool = True) -> None:
        for k, b in self._buttons.items():
            b.setChecked(k == key)
        if notify and self._handler:
            self._handler(key)
