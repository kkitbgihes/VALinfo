"""Общие хранилища: разобранные матчи и списки матчей игроков.

Главная идея оптимизации: каждый match-details скачивается и разбирается ОДИН раз на всё приложение,
даже если его запросили сразу несколько игроков/модулей (дедупликация «в полёте»), а в памяти остаётся только
компактный `MatchSummary`.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, Optional

from valinfo.analytics.records import MatchSummary, parse_match_summary
from valinfo.config import HISTORY_TTL_SEC, MATCH_STORE_MAX

log = logging.getLogger(__name__)


class MatchStore:
    def __init__(self, roles: Optional[dict] = None, max_items: int = MATCH_STORE_MAX):
        self.roles: dict = roles or {}
        self._max = max_items
        self._data: "OrderedDict[str, MatchSummary]" = OrderedDict()
        self._inflight: dict[str, Future] = {}
        self._lock = threading.Lock()
        self.fetches = 0           # сколько раз реально ходили в сеть (для тестов/диагностики)

    def peek(self, mid: str) -> Optional[MatchSummary]:
        with self._lock:
            s = self._data.get(mid)
            if s is not None:
                self._data.move_to_end(mid)
            return s

    def put(self, summary: Optional[MatchSummary]) -> None:
        """Положить уже разобранный матч (например, только что завершённый) — повторно скачивать не придётся."""
        if summary is None or not summary.mid:
            return
        with self._lock:
            self._data[summary.mid] = summary
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def get(self, riot, mid: str) -> Optional[MatchSummary]:
        with self._lock:
            s = self._data.get(mid)
            if s is not None:
                self._data.move_to_end(mid)
                return s
            fut = self._inflight.get(mid)
            owner = fut is None
            if owner:
                fut = self._inflight[mid] = Future()
        if not owner:
            return fut.result()               # кто-то уже качает этот матч — ждём его результат
        summary = None
        try:
            self.fetches += 1
            det = riot.get(f"{riot.pd}/match-details/v1/matches/{mid}")
            summary = parse_match_summary(det, self.roles) if det else None
            if summary is not None and not summary.mid:
                summary.mid = mid
                for r in summary.records.values():
                    r.mid = mid
        except Exception:
            log.debug("match %s failed", mid, exc_info=True)
        finally:
            with self._lock:
                if summary is not None:
                    self._data[mid] = summary
                    while len(self._data) > self._max:
                        self._data.popitem(last=False)
                self._inflight.pop(mid, None)
            fut.set_result(summary)
        return summary

    def get_many(self, riot, mids: Iterable[str], pool: ThreadPoolExecutor) -> list:
        mids = list(mids)
        return list(pool.map(lambda m: self.get(riot, m), mids))

    def __len__(self) -> int:
        return len(self._data)


@dataclass(frozen=True)
class HistEntry:
    mid: str
    started_ms: int
    queue: str


class HistoryStore:
    """Списки матчей игроков. Список в рамках одного лобби запрашивается один раз и переиспользуется
    и статистикой, и определением пати."""

    PAGE = 20

    def __init__(self, ttl: float = HISTORY_TTL_SEC):
        self._ttl = ttl
        self._data: dict = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def entries(self, riot, puuid: str, queue: Optional[str], count: int) -> list:
        """Последние `count` матчей игрока (queue=None — всех режимов). Кэш: хватает более длинного списка."""
        key = (puuid, queue)
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
        if hit and now - hit[0] < self._ttl and (len(hit[1]) >= count or hit[2]):
            return hit[1][:count]
        out: list = []
        exhausted = False
        for start in range(0, count, self.PAGE):
            end = min(start + self.PAGE, count)
            params = {"startIndex": start, "endIndex": end}
            if queue:
                params["queue"] = queue
            h = riot.get(f"{riot.pd}/match-history/v1/history/{puuid}", params=params)
            items = (h or {}).get("History") or []
            out += [HistEntry(m["MatchID"], m.get("GameStartTime") or 0, (m.get("QueueID") or queue or "").lower())
                    for m in items]
            if len(items) < end - start:
                exhausted = True
                break
        with self._lock:
            self._data[key] = (now, out, exhausted)
        return out
