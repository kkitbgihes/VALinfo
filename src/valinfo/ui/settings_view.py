"""Вкладка «Настройки»: язык, тема, определение страны/пати, глубина анализа, поведение окна.

Все изменения применяются сразу и сохраняются в settings.json. Каждая настройка — одна строка в `_build()`;
чтобы добавить новую, достаточно добавить ключ в `settings.DEFAULTS`, строку здесь и перевод в locales.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton, QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from valinfo.i18n import Translator
from valinfo.i18n import tr
from valinfo.settings import ANALYSIS_CHOICES, Settings
from valinfo.ui.analysis_tab import clear_layout, make_card
from valinfo.ui.theme import THEMES, c
from valinfo.ui.widgets import ToggleSwitch, font


class ThemeSwatch(QAbstractButton):
    """Миниатюра темы: фон, карточка с шапкой, ally/enemy-полоски и акцент."""

    W, H = 150, 92

    def __init__(self, key: str, selected: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self.sel = selected
        self.setFixedSize(self.W, self.H + 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e):
        t = THEMES[self.key]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.5, 1.5, self.W - 3, self.H - 3)
        p.setPen(QPen(QColor(c.accent if self.sel else c.border), 2.5 if self.sel else 1.2))
        p.setBrush(QColor(t.bg))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        card = QRectF(12, 12, self.W - 24, self.H - 26)
        p.setBrush(QColor(t.card))
        p.drawRoundedRect(card, 5, 5)
        p.setBrush(QColor(t.header))
        p.drawRoundedRect(QRectF(card.left(), card.top(), card.width(), 14), 5, 5)
        p.setBrush(QColor(t.ally))
        p.drawRect(QRectF(card.left(), card.top(), 3, 14))
        for i, col in enumerate((t.ally, t.enemy)):
            y = card.top() + 22 + i * 14
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(card.left() + 8, y, 3, 9), 1, 1)
            p.setBrush(QColor(t.muted))
            p.drawRoundedRect(QRectF(card.left() + 16, y + 3, card.width() * (0.55 if i == 0 else 0.4), 3), 1.5, 1.5)
        p.setBrush(QColor(t.accent))
        p.drawRoundedRect(QRectF(card.right() - 28, card.bottom() - 9, 22, 4), 2, 2)
        p.setPen(QColor(c.text if self.sel else c.muted))
        p.setFont(font(9, self.sel))
        p.drawText(QRectF(0, self.H + 2, self.W, 18), Qt.AlignmentFlag.AlignCenter, tr(f"theme.{self.key}"))
        p.end()


class SettingsView(QWidget):
    def __init__(self, settings: Settings, translator: Translator, parent=None):
        super().__init__(parent)
        self._s = settings
        self._tr = translator
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("PageBody")
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(12, 12, 12, 12)
        self._lay.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self._build()

    def rebuild(self) -> None:
        """Язык/тема сменились. Пересобираем «на следующем тике», чтобы не удалять виджет внутри его же обработчика."""
        QTimer.singleShot(0, self._build)

    # ── строки настроек ──
    def _row(self, title: str, desc: str, control: QWidget) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel(title)
        t.setFont(font(10, True))
        col.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setFont(font(9, False))
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {c.muted};")
            col.addWidget(d)
        lay.addLayout(col, 1)
        lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def _toggle(self, key: str) -> ToggleSwitch:
        sw = ToggleSwitch(bool(self._s.get(key)))
        sw.toggled.connect(lambda on, k=key: self._s.set(k, on))
        return sw

    def _combo(self, items: list, current, on_change: Callable) -> QComboBox:
        cb = QComboBox()
        for label, data in items:
            cb.addItem(label, data)
        idx = next((i for i, (_, d) in enumerate(items) if d == current), 0)
        cb.setCurrentIndex(idx)
        cb.activated.connect(lambda i, cb=cb: on_change(cb.itemData(i)))
        return cb

    def _sep(self) -> QFrame:
        f = QFrame()
        f.setFixedHeight(1)
        f.setStyleSheet(f"background-color: {c.border}; border: none;")
        return f

    def _build(self) -> None:
        clear_layout(self._lay)
        s = self._s

        # ── Внешний вид ──
        card, lay, _ = make_card(tr("settings.appearance"))
        langs = [(tr("settings.language.auto"), "auto")] + [(name, code) for code, name in self._tr.languages()]
        lay.addWidget(self._row(tr("settings.language"), tr("settings.language.desc"),
                                self._combo(langs, s.language, lambda v: s.set("language", v))))
        lay.addWidget(self._sep())
        theme_title = QLabel(tr("settings.theme"))
        theme_title.setFont(font(10, True))
        lay.addWidget(theme_title)
        desc = QLabel(tr("settings.theme.desc"))
        desc.setStyleSheet(f"color: {c.muted};")
        lay.addWidget(desc)
        row = QHBoxLayout()
        row.setSpacing(12)
        for key in THEMES:
            sw = ThemeSwatch(key, key == s.theme)
            sw.clicked.connect(lambda _=False, k=key: s.set("theme", k))
            row.addWidget(sw)
        row.addStretch()
        lay.addLayout(row)
        self._lay.addWidget(card)

        # ── Определение ──
        card, lay, _ = make_card(tr("settings.detection"))
        lay.addWidget(self._row(tr("settings.country"), tr("settings.country.desc"), self._toggle("country_detection")))
        lay.addWidget(self._sep())
        lay.addWidget(self._row(tr("settings.party"), tr("settings.party.desc"), self._toggle("party_detection")))
        lay.addWidget(self._sep())
        depth = [(tr("settings.depth.n", n=n), n) for n in ANALYSIS_CHOICES]
        lay.addWidget(self._row(tr("settings.depth"), tr("settings.depth.desc"),
                                self._combo(depth, s.analysis_matches, lambda v: s.set("analysis_matches", v))))
        self._lay.addWidget(card)

        # ── Интерфейс ──
        card, lay, _ = make_card(tr("settings.interface"))
        lay.addWidget(self._row(tr("settings.peak_date"), tr("settings.peak_date.desc"), self._toggle("show_peak_date")))
        lay.addWidget(self._sep())
        lay.addWidget(self._row(tr("settings.on_top"), tr("settings.on_top.desc"), self._toggle("always_on_top")))
        self._lay.addWidget(card)

        note = QLabel(tr("settings.saved_note"))
        note.setStyleSheet(f"color: {c.muted};")
        note.setFont(font(8, False))
        self._lay.addWidget(note)
        self._lay.addStretch()
