"""Классификация HTML-страницы tracker.gg → что мы на самом деле получили.

Чистая функция без побочных эффектов (тестируется без Edge и сети).
ВСЕ текстовые маркеры собраны здесь, в верхней части файла: если tracker.gg поменяет вёрстку/тексты,
правится только этот файл. Неопознанные страницы сохраняются в debug_pages (см. config.COUNTRY_DEBUG_DUMP).

Порядок проверок важен (сверху вниз, первое совпадение побеждает):

    1. флаг найден            → FOUND        (как в оригинале — самый приоритетный признак)
    2. Cloudflare / блокировка → BLOCKED      (сбой алгоритма, лечится повтором)
    3. «профиль приватный»     → PRIVATE      (факт про игрока)
    4. «игрок не найден»       → BAD_PAGE     (сбой: не та страница)
    5. страница пустая         → BAD_PAGE
    6. в тексте нет ника       → BAD_PAGE     (это не профиль нужного игрока: главная, ошибка, согласие и т.д.)
    7. профиль открыт, флага нет → NO_COUNTRY (кандидат; финально подтверждается фетчером повторной загрузкой)
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import Optional

from valinfo.models import CountryStatus

# ───────────────────────── МАРКЕРЫ ─────────────────────────
FLAG_RE = re.compile(r"https://trackercdn\.com/cdn/flags/4x3/([a-z]{2})\.svg", re.IGNORECASE)

# Приватный профиль (видимый текст страницы, регистр не важен). Намеренно узкие фразы:
# слова «Make private» из подвала/плашки tracker.gg НЕ должны срабатывать.
PRIVATE_TEXT_RE = re.compile(
    r"\b(?:is|are|been|set to|marked as|currently)\s+(?:set to\s+|marked as\s+)?private\b"
    r"|профил[а-я]*[^.!?]{0,40}(?:приватн|скрыт|закрыт|частн)(?:[аоы]|ый|ая|ое|ые)?(?![а-я])"
    r"|(?:приватн|закрыт|частн)(?:ый|ая|ое|ые)\s+профил",
    re.IGNORECASE,
)
PRIVATE_RAW_MARKERS = ("CollectorResultStatus::Private",)

# «Игрок не найден»
NOT_FOUND_TEXT_RE = re.compile(
    r"(?:player|profile|user)[^.!?]{0,40}\bnot found\b"
    r"|we (?:could not|couldn't|can't|cannot) find"
    r"|(?:игрок|профиль|пользовател[а-я]*)[^.!?]{0,40}не найден(?:а|о)?(?![а-я])"
    r"|не удалось найти",
    re.IGNORECASE,
)
NOT_FOUND_RAW_MARKERS = ("CollectorResultStatus::NotFound",)

# Cloudflare / антибот / rate limit (заголовок страницы)
BLOCK_TITLE_MARKERS = (
    "один момент", "just a moment", "attention required", "access denied", "доступ запрещен", "доступ запрещён",
    "too many requests", "error 1020", "403 forbidden", "429",
)
# Cloudflare / антибот (видимый текст)
BLOCK_TEXT_MARKERS = (
    "checking your browser", "verify you are human", "you have been blocked", "sorry, you have been blocked",
    "rate limited", "подтвердите, что вы человек", "проверка безопасности", "вы заблокированы",
)

MIN_VISIBLE_TEXT = 200   # короче — страница фактически пустая (не успела отрисоваться)
# ────────────────────────────────────────────────────────────

_SKIP_BLOCKS_RE = re.compile(r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class PageVerdict:
    status: CountryStatus
    code: Optional[str] = None
    detail: str = ""


def html_title(html: str) -> str:
    m = _TITLE_RE.search(html)
    return _WS_RE.sub(" ", _html.unescape(m.group(1))).strip() if m else ""


def visible_text(html: str) -> str:
    """Текст, который реально видит пользователь (без script/style/JSON-состояния приложения)."""
    cleaned = _SKIP_BLOCKS_RE.sub(" ", html)
    text = _html.unescape(_TAG_RE.sub(" ", cleaned))
    return _WS_RE.sub(" ", text).strip()


def find_country(html: str) -> Optional[str]:
    m = FLAG_RE.search(html)
    return m.group(1).upper() if m else None


def is_cloudflare(html: str) -> bool:
    """Исходная проверка из монолита — оставлена без изменений."""
    lower = html.lower()
    title = html_title(html).lower()
    if "один момент" in title or "just a moment" in title:
        return True
    return (
        "challenges.cloudflare.com" in lower
        and "noindex,nofollow" in lower
        and len(html) < 100_000
    )


def _is_blocked(html: str, title_l: str, text_l: str) -> Optional[str]:
    if is_cloudflare(html):
        return "Cloudflare challenge"
    for m in BLOCK_TITLE_MARKERS:
        if m in title_l:
            return f"Blocked page (title: {title_l[:60]!r})"
    if len(text_l) < 3000:  # текстовые маркеры блокировки имеют смысл только на коротких страницах
        for m in BLOCK_TEXT_MARKERS:
            if m in text_l:
                return f"Blocked page ({m})"
    return None


def classify_page(html: str, name: str, tag: str) -> PageVerdict:
    # 1. флаг — главный признак успеха, как и раньше
    code = find_country(html)
    if code:
        return PageVerdict(CountryStatus.FOUND, code)

    title = html_title(html)
    text = visible_text(html)
    title_l, text_l = title.lower(), text.lower()

    # 2. блокировка
    blocked = _is_blocked(html, title_l, text_l)
    if blocked:
        return PageVerdict(CountryStatus.BLOCKED, detail=blocked)

    # 3. приватный профиль
    if PRIVATE_TEXT_RE.search(text) or any(m in html for m in PRIVATE_RAW_MARKERS):
        return PageVerdict(CountryStatus.PRIVATE, detail="Profile is private on tracker.gg")

    # 4. игрок не найден
    if NOT_FOUND_TEXT_RE.search(text) or any(m in html for m in NOT_FOUND_RAW_MARKERS):
        return PageVerdict(CountryStatus.BAD_PAGE, detail="tracker.gg says the player was not found")

    # 5. пустая страница
    if len(text) < MIN_VISIBLE_TEXT:
        return PageVerdict(CountryStatus.BAD_PAGE,
                           detail=f"Page is empty or not rendered yet ({len(text)} chars, title: {title[:50]!r})")

    # 6. это вообще профиль нужного игрока?
    needle = _WS_RE.sub(" ", name).strip().lower()
    if needle and needle not in text_l:
        return PageVerdict(CountryStatus.BAD_PAGE,
                           detail=f"Unexpected page (player name not on it, title: {title[:50]!r})")

    # 7. профиль загружен, это он, флага нет
    return PageVerdict(CountryStatus.NO_COUNTRY, detail="Profile loaded, no country flag on it")
