"""Пользовательские настройки (вкладка «Настройки»). Без Qt — можно читать из любого потока.

Хранятся в одном маленьком settings.json в папке кэша. Любое изменение:
  1) проверяется и приводится к допустимому значению,
  2) сохраняется атомарно,
  3) рассылается подписчикам (`subscribe`) — UI и worker реагируют сразу, без перезапуска.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from valinfo import config as C
from valinfo.paths import SETTINGS_FILE

log = logging.getLogger(__name__)

ANALYSIS_CHOICES = (5, 10, 15, 20)

DEFAULTS: dict = {
    "language": "auto",                       # "auto" = язык системы (ru/en), иначе код файла из i18n/locales
    "theme": "valorant",
    "country_detection": C.COUNTRY_DETECTION,
    "party_detection": C.PARTY_DETECTION,
    "analysis_matches": C.ANALYSIS_MATCHES,
    "always_on_top": False,
    "show_peak_date": True,
}


def _coerce(key: str, value: Any, current: Any) -> Any:
    """Приводит значение к допустимому; при мусоре возвращает текущее."""
    if key in ("country_detection", "party_detection", "always_on_top", "show_peak_date"):
        return bool(value) if isinstance(value, (bool, int)) else current
    if key == "analysis_matches":
        try:
            v = int(value)
        except (TypeError, ValueError):
            return current
        return v if v in ANALYSIS_CHOICES else min(ANALYSIS_CHOICES, key=lambda c: abs(c - v))
    if key in ("language", "theme"):
        return value if isinstance(value, str) and value else current
    return current


class Settings:
    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else SETTINGS_FILE
        self._values: dict = dict(DEFAULTS)
        self._listeners: list[Callable[[str, Any], None]] = []
        self._lock = threading.Lock()

    # ── чтение ──
    def __getattr__(self, name: str) -> Any:
        values = self.__dict__.get("_values")
        if values is not None and name in values:
            return values[name]
        raise AttributeError(name)

    def get(self, key: str) -> Any:
        return self._values[key]

    def as_dict(self) -> dict:
        return dict(self._values)

    # ── загрузка / запись ──
    def load(self) -> "Settings":
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            for k in DEFAULTS:
                if k in raw:
                    self._values[k] = _coerce(k, raw[k], self._values[k])
        return self

    def _save(self) -> None:
        tmp = f"{self._path}.tmp"
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._values, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except OSError:
            log.warning("cannot write settings", exc_info=True)

    # ── изменение ──
    def set(self, key: str, value: Any) -> bool:
        """True, если значение реально изменилось."""
        if key not in DEFAULTS:
            raise KeyError(key)
        with self._lock:
            new = _coerce(key, value, self._values[key])
            if new == self._values[key]:
                return False
            self._values[key] = new
            self._save()
        for cb in list(self._listeners):
            try:
                cb(key, new)
            except Exception:
                log.exception("settings listener failed for %s", key)
        return True

    def subscribe(self, cb: Callable[[str, Any], None]) -> None:
        self._listeners.append(cb)

    def unsubscribe(self, cb: Callable[[str, Any], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    @property
    def path(self) -> Path:
        return self._path


_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Глобальный экземпляр (создаётся и читается с диска при первом обращении)."""
    global _instance
    if _instance is None:
        _instance = Settings().load()
    return _instance


def set_settings(s: Optional[Settings]) -> None:
    """Для тестов: подменить глобальный экземпляр."""
    global _instance
    _instance = s
