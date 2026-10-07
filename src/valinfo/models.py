"""Общие модели данных (без Qt)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CountryStatus(str, Enum):
    LOADING = "loading"          # идёт поиск
    FOUND = "found"              # страна определена
    PRIVATE = "private"          # профиль на tracker.gg приватный — страны не видно
    NO_COUNTRY = "no_country"    # профиль открыт, но страна в нём не указана
    BLOCKED = "blocked"          # наш алгоритм: Cloudflare / rate limit
    BAD_PAGE = "bad_page"        # наш алгоритм: tracker вернул не ту страницу
    ERROR = "error"              # наш алгоритм: Edge не найден / упал / таймаут / сеть
    SKIPPED = "skipped"          # не искали (это вы, ник скрыт, не успели начать)
    INTERRUPTED = "interrupted"  # матч закончился раньше, чем поиск завершился

    @property
    def is_failure(self) -> bool:
        """Сбой самого алгоритма (а не факт про игрока)."""
        return self in (CountryStatus.BLOCKED, CountryStatus.BAD_PAGE, CountryStatus.ERROR)

    @property
    def is_cacheable(self) -> bool:
        return self in (CountryStatus.FOUND, CountryStatus.PRIVATE, CountryStatus.NO_COUNTRY)


@dataclass(frozen=True)
class CountryResult:
    status: CountryStatus
    code: Optional[str] = None      # ISO-2 для FOUND
    detail: Optional[str] = None    # человекочитаемая причина

    def to_dict(self) -> dict:
        return {"status": self.status.value, "code": self.code, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CountryResult":
        if not isinstance(data, dict):
            return cls(CountryStatus.SKIPPED, detail="No country data")
        try:
            status = CountryStatus(data.get("status"))
        except ValueError:
            status = CountryStatus.SKIPPED
        return cls(status, data.get("code"), data.get("detail"))
