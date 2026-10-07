"""Вкладка «Матч»: три состояния — ожидание (пустой экран), live-таблица, итоги матча.

Данные приходят всегда, но рисуются только когда вкладка видна («грязный флаг»): пока пользователь в настройках,
таблицы не перерисовываются, а при возврате показывается самое свежее состояние.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from valinfo.i18n import tr
from valinfo.ui.charts import EmptyState
from valinfo.ui.icons import IconProvider
from valinfo.ui.live_view import LiveView
from valinfo.ui.post_view import PostMatchView

IDLE, LIVE, POST = "idle", "live", "post"


class LiveTab(QWidget):
    def __init__(self, icons: IconProvider, parent=None):
        super().__init__(parent)
        self._state = IDLE
        self._data: Optional[dict] = None
        self._active = True
        self._dirty = True

        self.idle_view = EmptyState()
        self.live_view = LiveView(icons)
        self.post_view = PostMatchView(icons)
        self.post_view.back_requested.connect(self.set_idle)

        self.stack = QStackedWidget()
        for w in (self.idle_view, self.live_view, self.post_view):
            self.stack.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self._texts()
        self._render()

    @property
    def state(self) -> str:
        return self._state

    # ── смена состояния ──
    def set_idle(self) -> None:
        if self._state == IDLE and self._data is None:
            return
        self._state, self._data, self._dirty = IDLE, None, True
        if self._active:
            self._render()

    def set_live(self, data: dict) -> None:
        self._state, self._data, self._dirty = LIVE, data, True
        if self._active:
            self._render()

    def set_post(self, data: dict) -> None:
        self._state, self._data, self._dirty = POST, data, True
        if self._active:
            self._render()

    def set_active(self, on: bool) -> None:
        self._active = on
        if on and self._dirty:
            self._render()

    def on_no_match(self) -> None:
        """Матча нет. Итоги матча не трогаем — их уберёт кнопка «назад»."""
        if self._state == LIVE:
            self.set_idle()

    # ── отрисовка ──
    def _texts(self) -> None:
        self.idle_view.set_texts(tr("idle.title"), tr("idle.sub"))

    def _render(self) -> None:
        self._dirty = False
        if self._state == LIVE and self._data is not None:
            self.live_view.update_data(self._data)
            self.stack.setCurrentWidget(self.live_view)
        elif self._state == POST and self._data is not None:
            self.post_view.show_data(self._data)
            self.stack.setCurrentWidget(self.post_view)
        else:
            self.live_view.clear()                 # никаких обрывков прошлого матча
            self.stack.setCurrentWidget(self.idle_view)

    def retheme(self) -> None:
        self._texts()
        self.idle_view.restyle()
        self.live_view.retheme()
        self.post_view.retranslate()
        self._dirty = True
        if self._active:
            self._render()
