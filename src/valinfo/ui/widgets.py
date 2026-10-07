"""Переиспользуемые виджеты: спиннер, кликабельный ник с анимацией копирования, иконки статусов, ячейки таблиц."""
from __future__ import annotations

import html
import time
import weakref
from functools import lru_cache
from typing import Optional

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from valinfo.i18n import tr
from valinfo.ui.theme import c


@lru_cache(maxsize=None)
def font(size: int = 9, bold: bool = False) -> QFont:
    """Кэш QFont: раньше шрифт создавался заново для каждой из сотен ячеек при каждой перерисовке."""
    return QFont("Segoe UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal)


# ═══════════════════════════ СПИННЕР ═══════════════════════════
class _SpinClock(QObject):
    """Один общий таймер на все спиннеры (раньше у каждого был свой QTimer на 25 FPS).
    Сам останавливается, когда крутить нечего."""

    _inst: Optional["_SpinClock"] = None

    @classmethod
    def instance(cls) -> "_SpinClock":
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self):
        super().__init__()
        self._widgets: "weakref.WeakSet" = weakref.WeakSet()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    def register(self, w: QWidget) -> None:
        self._widgets.add(w)
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        alive = False
        for w in list(self._widgets):
            try:
                alive = True
                if w.isVisible():
                    w.update()
            except RuntimeError:  # C++ объект уже удалён
                self._widgets.discard(w)
        if not alive:
            self._timer.stop()


class SpinnerWidget(QWidget):
    """Маленький крутящийся индикатор. Фаза считается от времени, а не от создания виджета,
    поэтому анимация не «перезапускается», когда таблица перерисовывается."""

    def __init__(self, size=16, color="#FFB84D", parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(color)
        _SpinClock.instance().register(self)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = 2
        rect = QRectF(w, w, self.width() - 2 * w, self.height() - 2 * w)
        track = QColor(self._color)
        track.setAlpha(55)
        p.setPen(QPen(track, w))
        p.drawEllipse(rect)
        pen = QPen(self._color, w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        angle = int((time.monotonic() * 540) % 360)
        p.drawArc(rect, -angle * 16, -110 * 16)
        p.end()


# ═══════════════════════════ ИКОНКИ СТАТУСОВ ═══════════════════════════
class StatusGlyph(QWidget):
    """Векторные иконки (рисуются в сетке 18×18 и масштабируются) — не зависят от emoji-шрифтов."""

    GRID = 18.0

    def __init__(self, kind: str, color: str, size: int = 18, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self.width() / self.GRID
        p.scale(k, k)
        c = self._color
        soft = QColor(c)
        soft.setAlpha(55)
        pen = QPen(c, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        if self._kind == "lock":
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(5.5, 2.5, 7, 7.5), 0, 180 * 16)             # дужка
            p.drawLine(QPointF(5.5, 6.3), QPointF(5.5, 8.5))
            p.drawLine(QPointF(12.5, 6.3), QPointF(12.5, 8.5))
            p.setBrush(soft)
            p.drawRoundedRect(QRectF(3.5, 8.2, 11, 7.8), 2, 2)           # корпус
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(9, 11.6), 1.25, 1.25)                  # замочная скважина
            p.drawRect(QRectF(8.5, 11.8, 1, 2.4))

        elif self._kind == "flag_off":
            p.setPen(pen)
            p.drawLine(QPointF(4.5, 2.5), QPointF(4.5, 16))              # древко
            dashed = QPen(c, 1.3, Qt.PenStyle.DashLine)
            dashed.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(dashed)
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(4.5, 3.2)
            path.lineTo(14.5, 3.2)
            path.lineTo(14.5, 10.2)
            path.lineTo(4.5, 10.2)
            p.drawPath(path)

        elif self._kind == "warning":
            p.setPen(pen)
            p.setBrush(soft)
            tri = QPainterPath()
            tri.moveTo(9, 2.4)
            tri.lineTo(16.2, 15.2)
            tri.lineTo(1.8, 15.2)
            tri.closeSubpath()
            p.drawPath(tri)
            p.setPen(QPen(c, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(9, 6.9), QPointF(9, 10.6))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(9, 12.8), 1.0, 1.0)

        elif self._kind == "dots":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            for x in (4.5, 9, 13.5):
                p.drawEllipse(QPointF(x, 9), 1.5, 1.5)
        p.end()


# ═══════════════════ КОПИРОВАНИЕ НИКА (кликабельный ник) ═══════════════════
COPY_ANIM_MS = 1500
_last_copy = {"key": None, "t": 0.0}   # чтобы анимация «доигрывалась», если строку перерисовали посреди неё


def _ease_out_back(x: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


class CopiedBadge(QWidget):
    """Кружок с «рисующейся» галочкой: выскакивает с лёгким овершутом, галочка прорисовывается, затем плавно гаснет."""

    SIZE = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._t = -1.0

    def set_progress(self, t: float) -> None:
        self._t = t
        self.update()

    def paintEvent(self, _event):
        t = self._t
        if t < 0 or t >= 1:
            return
        if t < 0.12:
            alpha = t / 0.12
        elif t > 0.80:
            alpha = max(0.0, (1.0 - t) / 0.20)
        else:
            alpha = 1.0
        scale = 0.55 + 0.45 * _ease_out_back(min(1.0, t / 0.25))

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(alpha)
        half = self.SIZE / 2
        p.translate(half, half)
        p.scale(scale, scale)
        p.translate(-half, -half)

        green = QColor(c.win)
        fill = QColor(green)
        fill.setAlpha(46)
        p.setPen(QPen(green, 1.4))
        p.setBrush(fill)
        p.drawEllipse(QRectF(1, 1, 14, 14))

        prog = max(0.0, min(1.0, (t - 0.12) / 0.28))
        a, b, pc = QPointF(4.4, 8.4), QPointF(7.0, 11.0), QPointF(11.6, 5.6)
        len_ab = ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5
        len_bc = ((pc.x() - b.x()) ** 2 + (pc.y() - b.y()) ** 2) ** 0.5
        draw = prog * (len_ab + len_bc)
        pen = QPen(green, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if draw > 0:
            if draw <= len_ab:
                f = draw / len_ab
                p.drawLine(a, QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f))
            else:
                f = (draw - len_ab) / len_bc
                p.drawLine(a, b)
                p.drawLine(b, QPointF(b.x() + (pc.x() - b.x()) * f, b.y() + (pc.y() - b.y()) * f))
        p.end()


class NickLabel(QWidget):
    """Ник, по клику на который `nickname#tag` копируется в буфер обмена.
    Рядом с ником (место под значок зарезервировано, поэтому раскладка не «прыгает») проигрывается
    анимация подтверждения; сам ник на мгновение подсвечивается зелёным."""

    def __init__(self, display: str, copy_text: str, color: Optional[str] = None, bold: bool = False, parent=None):
        super().__init__(parent)
        color = color or c.text
        self._copy_text = copy_text
        self._color = color
        self._anim: Optional[QVariantAnimation] = None
        self._flash = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._lbl = QLabel(display)
        self._font = QFont(font(9, bold))
        self._lbl.setFont(self._font)
        self._lbl.setStyleSheet(f"color: {color};")
        self._lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._badge = CopiedBadge(self)
        lay.addWidget(self._lbl)
        lay.addWidget(self._badge)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"<b>{tr('common.click_to_copy')}</b><br>{html.escape(copy_text)}")

        # строку могли перерисовать посреди анимации — доигрываем с того же места
        if _last_copy["key"] == copy_text:
            elapsed = (time.monotonic() - _last_copy["t"]) * 1000
            if elapsed < COPY_ANIM_MS:
                self._play(elapsed)

    # ── hover ──
    def enterEvent(self, event):
        self._font.setUnderline(True)
        self._lbl.setFont(self._font)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._font.setUnderline(False)
        self._lbl.setFont(self._font)
        super().leaveEvent(event)

    # ── клик ──
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            QGuiApplication.clipboard().setText(self._copy_text)
            _last_copy["key"], _last_copy["t"] = self._copy_text, time.monotonic()
            self._play(0)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── анимация ──
    def _play(self, offset_ms: float) -> None:
        if self._anim is None:
            self._anim = QVariantAnimation(self)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.setDuration(COPY_ANIM_MS)
            self._anim.valueChanged.connect(self._on_anim)
            self._anim.finished.connect(self._on_done)
        self._anim.stop()
        self._anim.start()
        if offset_ms:
            self._anim.setCurrentTime(int(offset_ms))

    def _set_flash(self, on: bool) -> None:
        if on != self._flash:
            self._flash = on
            self._lbl.setStyleSheet(f"color: {c.win if on else self._color};")

    def _on_anim(self, value) -> None:
        t = float(value)
        self._badge.set_progress(t)
        self._set_flash(0.04 < t < 0.70)

    def _on_done(self) -> None:
        self._badge.set_progress(-1.0)
        self._set_flash(False)


# ═══════════════════════════ ЯЧЕЙКИ ТАБЛИЦ ═══════════════════════════
def create_cell(text, pixmap=None, text_color=None, is_bold=False, bar_color=None, sub_text=None,
                sub_tip=None) -> QWidget:
    """Ячейка таблицы: [цветная полоска] [иконка] текст [мелкая приглушённая подпись]."""
    text_color = text_color or c.text
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setSpacing(6)

    if bar_color is None:
        layout.setContentsMargins(6, 2, 6, 2)
    else:
        layout.setContentsMargins(0, 0, 6, 0)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background-color: {bar_color or 'transparent'}; border: none;")
        layout.addWidget(bar)

    if pixmap:
        lbl_img = QLabel()
        lbl_img.setPixmap(pixmap)
        layout.addWidget(lbl_img)

    lbl_text = QLabel(str(text))
    lbl_text.setFont(font(9, is_bold))
    lbl_text.setStyleSheet(f"color: {text_color};")
    layout.addWidget(lbl_text)
    if sub_text:
        lbl_sub = QLabel(str(sub_text))
        lbl_sub.setFont(font(8, False))
        lbl_sub.setStyleSheet(f"color: {c.muted};")
        if sub_tip:
            lbl_sub.setToolTip(sub_tip)
        layout.addWidget(lbl_sub)
    layout.addStretch()
    return widget


def create_nick_cell(display: str, copy_text: Optional[str], text_color=None, is_bold=False) -> QWidget:
    """Ячейка с ником. Если `copy_text` задан — ник кликабелен (копирует «nickname#tag»)."""
    text_color = text_color or c.text
    if not copy_text:
        return create_cell(display, text_color=text_color, is_bold=is_bold)
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(6, 2, 6, 2)
    layout.addWidget(NickLabel(display, copy_text, text_color, is_bold))
    layout.addStretch()
    return widget


# ═══════════════════════════ ЗАГОЛОВОК ОКНА ═══════════════════════════
class CustomTitleBar(QFrame):
    """Кастомный заголовок без стандартной виндосовской рамки с поддержкой перетаскивания окна"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CustomTitleBar")
        self.setFixedHeight(30)
        self._drag_pos = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 4, 0)
        layout.setSpacing(4)

        title_lbl = QLabel("ValInfo - by khs")
        self._btn_min = None
        title_lbl.setFont(font(9, True))
        title_lbl.setStyleSheet(f"color: {c.muted};")

        btn_min = QPushButton("─")
        btn_min.setObjectName("TitleBtn")
        btn_min.setToolTip(tr("window.minimize"))
        btn_min.clicked.connect(self._minimize)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("TitleBtnClose")
        btn_close.setToolTip(tr("window.close"))
        btn_close.clicked.connect(self._close)

        self._btn_min, self._btn_close = btn_min, btn_close
        layout.addWidget(title_lbl)
        layout.addStretch()
        layout.addWidget(btn_min)
        layout.addWidget(btn_close)

    def retranslate(self) -> None:
        self._btn_min.setToolTip(tr("window.minimize"))
        self._btn_close.setToolTip(tr("window.close"))

    def _minimize(self):
        win = self.window()
        if win:
            win.showMinimized()

    def _close(self):
        win = self.window()
        if win:
            win.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win:
                self._drag_pos = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            win = self.window()
            if win:
                win.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ═══════════════════════════ ПЕРЕКЛЮЧАТЕЛЬ ═══════════════════════════
class ToggleSwitch(QAbstractButton):
    """Переключатель «вкл/выкл» (как в мобильных ОС). Состояние — `isChecked()`, сигнал — `toggled(bool)`."""

    W, H = 42, 22

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _on_anim(self, v) -> None:
        self._pos = float(v)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(c.border), QColor(c.accent)
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, self.W, self.H), self.H / 2, self.H / 2)
        p.setBrush(QColor("#FFFFFF"))
        d = self.H - 6
        p.drawEllipse(QRectF(3 + (self.W - d - 6) * t, 3, d, d))
        p.end()
