"""Дисковый кэш стран.

 • найденная страна        — навсегда (как и раньше);
 • «приватный профиль» и «страна не указана» — на COUNTRY_NEGATIVE_TTL_HOURS (игрок мог открыть профиль/указать страну);
 • сбои алгоритма (Cloudflare, не та страница, Edge) — НЕ кэшируются.

Формат файла обратно совместим: старые записи вида {"nick#tag": "RU"} читаются как FOUND.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from valinfo.models import CountryResult, CountryStatus

log = logging.getLogger(__name__)


def make_key(name: str, tag: str) -> str:
    return f"{name}#{tag}".lower()


class CountryCache:
    def __init__(self, path: Path, negative_ttl_hours: float):
        self._path = Path(path)
        self._ttl = negative_ttl_hours * 3600
        self._lock = threading.Lock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get(self, key: str, now: Optional[float] = None) -> Optional[CountryResult]:
        with self._lock:
            raw = self._data.get(key)
        if raw is None:
            return None
        if isinstance(raw, str):  # старый формат
            return CountryResult(CountryStatus.FOUND, raw.upper()) if len(raw) == 2 else None
        if not isinstance(raw, dict):
            return None
        try:
            status = CountryStatus(raw.get("s"))
        except ValueError:
            return None
        if status is CountryStatus.FOUND:
            code = raw.get("c")
            return CountryResult(status, code.upper()) if code else None
        if not status.is_cacheable:
            return None
        if (now if now is not None else time.time()) - raw.get("t", 0) > self._ttl:
            return None
        return CountryResult(status, detail=raw.get("d"))

    def put(self, key: str, result: CountryResult, now: Optional[float] = None) -> None:
        if not result.status.is_cacheable:
            return
        entry = {"s": result.status.value, "t": now if now is not None else time.time()}
        if result.code:
            entry["c"] = result.code
        if result.detail and result.status is not CountryStatus.FOUND:
            entry["d"] = result.detail
        with self._lock:
            self._data[key] = entry
            self._flush_locked()

    def _flush_locked(self) -> None:
        tmp = f"{self._path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError:
            log.warning("cannot write country cache", exc_info=True)
