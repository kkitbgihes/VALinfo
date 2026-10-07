"""Фоновые вычисления для вкладок «Прогноз» и «Игроки».

• Считается ТОЛЬКО по запросу вкладки (то есть пока вкладка открыта) — скрытые вкладки процессор не грузят.
• Результат кэшируется по «подписи» входных данных: пока ничего не изменилось, повторный заход на вкладку
  показывает готовое мгновенно.
• Работает в одном фоновом потоке: интерфейс не подвисает, а запросы не накладываются друг на друга
  (если пока считалось пришли новые данные — после завершения запускается ровно один свежий расчёт).
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from valinfo.analytics.lobby import build_lobby, payload_signature
from valinfo.analytics.predictor import analyze
from valinfo.analytics.profile import analyze_players
from valinfo.i18n import tr

log = logging.getLogger(__name__)


class AnalysisService(QObject):
    done = pyqtSignal(str, object, object)       # (вид, подпись, результат | Exception)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self._lock = threading.Lock()
        self._cache: dict = {}                   # вид -> (подпись, результат)
        self._running: dict = {}                 # вид -> подпись, которая считается сейчас
        self._pending: dict = {}                 # вид -> (payload, подпись) — самый свежий запрос, пока идёт расчёт

    def cached(self, kind: str, sig):
        with self._lock:
            hit = self._cache.get(kind)
        return hit[1] if hit and hit[0] == sig else None

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            self._pending.clear()

    def request(self, kind: str, payload: dict) -> Optional[object]:
        """Вернёт готовый результат из кэша или None (тогда придёт сигнал `done`)."""
        sig = payload_signature(payload, kind)
        hit = self.cached(kind, sig)
        if hit is not None:
            return hit
        with self._lock:
            if self._running.get(kind) == sig:
                return None
            if kind in self._running:
                self._pending[kind] = (payload, sig)     # перезаписываем: нужен только самый свежий
                return None
            self._running[kind] = sig
        self._pool.submit(self._work, kind, payload, sig)
        return None

    def _work(self, kind: str, payload: dict, sig) -> None:
        try:
            lobby = build_lobby(payload, tr("common.hidden"))
            result = analyze(lobby) if kind == "prediction" else analyze_players(lobby)
            result = (lobby, result)
            with self._lock:
                self._cache[kind] = (sig, result)
        except Exception as e:                                # noqa: BLE001 — показываем пользователю, не роняем поток
            log.exception("analysis %s failed", kind)
            result = e
        with self._lock:
            self._running.pop(kind, None)
            nxt = self._pending.pop(kind, None)
        self.done.emit(kind, sig, result)
        if nxt is not None:
            self.request(kind, nxt[0])

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
