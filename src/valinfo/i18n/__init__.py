r"""Локализация: один JSON-файл = один язык.

Как добавить язык
-----------------
1. Скопируйте `locales/en.json` в `locales/<код>.json` (например `de.json`).
2. В блоке `_meta` поменяйте `code` и `name` (имя показывается в настройках на самом языке).
3. Переведите значения (ключи НЕ трогайте; `{name}`-подстановки оставляйте как есть).
Всё. Язык появится в настройках автоматически. Недостающие ключи берутся из английского.

Можно и без пересборки exe: положите файл в `%LOCALAPPDATA%\ValInfoCache\locales\`.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from valinfo.paths import USER_LOCALES_DIR

log = logging.getLogger(__name__)

FALLBACK = "en"
META_KEY = "_meta"


@dataclass
class Language:
    code: str
    name: str
    strings: dict = field(default_factory=dict)


def _builtin_dirs() -> list[Path]:
    dirs = [Path(__file__).resolve().parent / "locales"]
    base = getattr(sys, "_MEIPASS", None)           # PyInstaller
    if base:
        dirs.append(Path(base) / "valinfo" / "i18n" / "locales")
    return dirs


class Translator:
    def __init__(self, extra_dirs: Optional[list[Path]] = None):
        self._dirs = _builtin_dirs() + [USER_LOCALES_DIR] + list(extra_dirs or [])
        self._langs: dict[str, Language] = {}
        self._current = FALLBACK
        self._listeners: list[Callable[[str], None]] = []
        self.reload()

    # ── загрузка ──
    def reload(self) -> None:
        langs: dict[str, Language] = {}
        for d in self._dirs:                          # поздние папки перекрывают ранние (пользовательские > встроенные)
            try:
                files = sorted(d.glob("*.json"))
            except OSError:
                continue
            for f in files:
                try:
                    with open(f, encoding="utf-8") as fh:
                        raw = json.load(fh)
                    meta = raw.pop(META_KEY, {}) or {}
                    code = str(meta.get("code") or f.stem).lower()
                    langs[code] = Language(code, str(meta.get("name") or code), {k: str(v) for k, v in raw.items()})
                except Exception:
                    log.warning("bad locale file %s", f, exc_info=True)
        self._langs = langs
        if self._current not in self._langs:
            self._current = FALLBACK if FALLBACK in self._langs else (next(iter(self._langs), FALLBACK))

    # ── выбор языка ──
    @property
    def current(self) -> str:
        return self._current

    def languages(self) -> list[tuple[str, str]]:
        """[(код, имя)] — en первым, дальше по алфавиту кода."""
        items = sorted(self._langs.values(), key=lambda l: (l.code != FALLBACK, l.code))
        return [(l.code, l.name) for l in items]

    def resolve(self, code: str, system_hint: str = "") -> str:
        """'auto' → язык системы (по подсказке вроде 'ru_RU'), иначе сам код, если такой язык есть."""
        if code == "auto":
            hint = (system_hint or "").lower().replace("-", "_").split("_")[0]
            return hint if hint in self._langs else FALLBACK
        return code if code in self._langs else FALLBACK

    def set_language(self, code: str) -> bool:
        code = code if code in self._langs else FALLBACK
        if code == self._current:
            return False
        self._current = code
        for cb in list(self._listeners):
            try:
                cb(code)
            except Exception:
                log.exception("language listener failed")
        return True

    def on_change(self, cb: Callable[[str], None]) -> None:
        self._listeners.append(cb)

    # ── перевод ──
    def tr(self, key: str, **params) -> str:
        cur = self._langs.get(self._current)
        text = cur.strings.get(key) if cur else None
        if text is None:
            fb = self._langs.get(FALLBACK)
            text = fb.strings.get(key) if fb else None
        if text is None:
            return key
        if params:
            try:
                return text.format(**params)
            except (KeyError, IndexError, ValueError):
                return text
        return text

    def keys(self, code: str) -> set:
        l = self._langs.get(code)
        return set(l.strings) if l else set()


translator = Translator()
tr = translator.tr
