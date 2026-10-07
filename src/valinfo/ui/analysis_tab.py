"""База для вкладок, которые считают тяжёлое («Прогноз», «Игроки»).

Жизненный цикл:
  set_payload(payload)  — MainWindow сообщает, что live-данные изменились (дёшево: просто запоминаем);
  set_active(True)      — вкладку открыли: только теперь запрашиваем расчёт (или берём готовый из кэша);
  on_done(...)          — фоновый расчёт завершился → рисуем.
Пока вкладка скрыта, процессор она не тратит вообще.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QStackedLayout, QVBoxLayout, QWidget

from valinfo.analytics.lobby import loading_progress, payload_signature
from valinfo.i18n import tr
from valinfo.ui.analysis_service import AnalysisService
from valinfo.ui.charts import EmptyState
from valinfo.ui.icons import IconProvider
from valinfo.ui.theme import c
from valinfo.ui.widgets import font


def make_card(title: str = "", accent: Optional[str] = None):
    """Карточка с заголовком. → (frame, layout для содержимого, label заголовка)."""
    card = QFrame()
    card.setObjectName("Card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    lbl = QLabel(title)
    lbl.setFont(font(10, True))
    lbl.setStyleSheet(f"color: {accent or c.title}; letter-spacing: 1px;")
    if title:
        lay.addWidget(lbl)
    return card, lay, lbl


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class AnalysisTabBase(QWidget):
    KIND = ""

    def __init__(self, service: AnalysisService, icons: IconProvider, parent=None):
        super().__init__(parent)
        self._svc = service
        self._icons = icons
        self._payload: Optional[dict] = None
        self._active = False
        self._result = None             # (lobby, результат), который сейчас нарисован
        self._result_mid = None
        self._wanted_sig = None

        self._stack = QStackedLayout(self)
        self._empty = EmptyState()
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._body.setObjectName("PageBody")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(12, 12, 12, 12)
        self._body_lay.setSpacing(12)
        self._scroll.setWidget(self._body)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._scroll)
        self._stack.setCurrentWidget(self._empty)
        self._service_conn = service.done.connect(self._on_done)

    # ── то, что реализуют наследники ──
    def precheck(self, payload: Optional[dict]) -> Optional[tuple]:
        """None — можно считать; иначе (заголовок, пояснение) для заглушки."""
        raise NotImplementedError

    def render(self, lobby, result, payload: dict) -> None:
        raise NotImplementedError

    # ── внешний API ──
    def set_payload(self, payload: Optional[dict]) -> None:
        self._payload = payload
        if self._active:
            self.refresh()

    def set_active(self, on: bool) -> None:
        self._active = on
        if on:
            self.refresh()

    def retheme(self) -> None:
        self._empty.restyle()
        if self._result is not None and self._payload is not None:
            self.render(*self._result, self._payload)
        elif self._active:
            self.refresh()

    # ── логика ──
    def _show_message(self, title: str, sub: str) -> None:
        self._empty.set_texts(title, sub)
        self._stack.setCurrentWidget(self._empty)

    def refresh(self) -> None:
        payload = self._payload
        msg = self.precheck(payload)
        if msg is not None:
            self._result, self._result_mid = None, None
            self._show_message(*msg)
            return
        mid = payload.get("mid")
        if self._result_mid != mid:                   # другой матч — старое показывать нельзя
            self._result, self._result_mid = None, None
        self._wanted_sig = payload_signature(payload, self.KIND)
        hit = self._svc.request(self.KIND, payload)
        if hit is not None:
            self._display(hit, payload)
        elif self._result is None:
            done, total = loading_progress(payload)
            self._show_message(tr("analysis.calculating"), tr("analysis.loaded", done=done, total=total))

    def _on_done(self, kind: str, sig, result) -> None:
        if kind != self.KIND or not self._active or sig != self._wanted_sig or self._payload is None:
            return
        if isinstance(result, Exception):
            self._show_message(tr("analysis.error"), f"{type(result).__name__}: {result}")
            return
        self._display(result, self._payload)

    def _display(self, result, payload: dict) -> None:
        self._result, self._result_mid = result, payload.get("mid")
        self.render(*result, payload)
        self._stack.setCurrentWidget(self._scroll)
