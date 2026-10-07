"""Очередь задач «ник#тег → страна». Идея прежняя: headless Edge открывает профиль на tracker.gg,
из DOM достаётся флаг. Что изменилось:

 • результат — `CountryResult` со статусом (найдено / приватный / страна не указана / блок / не та страница / ошибка);
 • «приватный профиль» и «страна не указана» определяются сразу или за 2 загрузки, а не после 5 бесполезных попыток;
 • воркеры стартуют лениво (при первой задаче), кэш-попадания отдаются мгновенно, не стоя в очереди за Edge;
 • кэш с TTL для негативных результатов, атомарная запись.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote

from valinfo.config import (
    COUNTRY_ATTEMPTS, COUNTRY_DEBUG_DUMP, COUNTRY_DEBUG_DUMP_KEEP, COUNTRY_NEGATIVE_TTL_HOURS,
    COUNTRY_NO_FLAG_CONFIRMATIONS, COUNTRY_START_WAIT, COUNTRY_WORKERS,
)
from valinfo.country.cache import CountryCache, make_key
from valinfo.country.classify import classify_page, html_title
from valinfo.country.edge import find_edge, run_edge
from valinfo.models import CountryResult, CountryStatus
from valinfo.paths import COUNTRY_CACHE_FILE, DEBUG_PAGES_DIR, EDGE_PROFILES_DIR
from valinfo.storage.assets import AssetStore

log = logging.getLogger(__name__)

Cancelled = Callable[[], bool]
OnDone = Callable[[CountryResult], None]


def tracker_url(name: str, tag: str) -> str:
    return f"https://tracker.gg/valorant/profile/riot/{quote(name, safe='')}%23{quote(tag, safe='')}"


class CountryFetcher:
    def __init__(self, assets: AssetStore, workers: int = COUNTRY_WORKERS):
        self._assets = assets
        self._q: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self._cache = CountryCache(COUNTRY_CACHE_FILE, COUNTRY_NEGATIVE_TTL_HOURS)
        self._edge: Optional[str] = None
        self._edge_checked = False
        self._n_workers = max(1, workers)
        self._started = False

    # ── публичный API ──
    def lookup(self, name: str, tag: str) -> Optional[CountryResult]:
        """Только из кэша, без сети (для пост-матча)."""
        return self._cache.get(make_key(name, tag))

    def submit(self, name: str, tag: str, cancelled: Cancelled, on_done: OnDone) -> None:
        """on_done(CountryResult) вызывается из потока-воркера (а для кэш-попаданий — сразу, из вызывающего)."""
        cached = self._cache.get(make_key(name, tag))
        if cached and (cached.status is not CountryStatus.FOUND or self._assets.has_flag(cached.code)):
            on_done(cached)
            return
        self._ensure_started()
        self._q.put((name, tag, cancelled, on_done))

    # ── внутреннее ──
    def _ensure_started(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        for i in range(self._n_workers):
            threading.Thread(target=self._loop, args=(i,), daemon=True, name=f"country-{i}").start()

    def _get_edge(self) -> Optional[str]:
        with self._lock:
            if not self._edge_checked:
                self._edge = find_edge()
                self._edge_checked = True
            return self._edge

    @staticmethod
    def _sleep(sec: float, cancelled: Cancelled) -> None:
        end = time.monotonic() + sec
        while time.monotonic() < end and not cancelled():
            time.sleep(0.2)

    def _loop(self, slot: int) -> None:
        profile = EDGE_PROFILES_DIR / f"slot{slot}"
        while True:
            name, tag, cancelled, on_done = self._q.get()
            if cancelled():
                continue
            try:
                result = self._resolve(name, tag, profile, cancelled)
            except Exception as e:
                log.exception("country resolve crashed for %s#%s", name, tag)
                result = CountryResult(CountryStatus.ERROR, detail=f"{type(e).__name__}: {e}")
            if cancelled():
                continue
            try:
                on_done(result)
            except Exception:
                log.exception("country on_done failed")

    def _dump(self, status: CountryStatus, name: str, html: str) -> None:
        if not COUNTRY_DEBUG_DUMP:
            return
        try:
            DEBUG_PAGES_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w-]+", "_", name)[:24] or "player"
            (DEBUG_PAGES_DIR / f"{status.value}_{safe}_{int(time.time())}.html").write_text(
                html, encoding="utf-8", errors="replace")
            files = sorted(DEBUG_PAGES_DIR.glob("*.html"), key=lambda f: f.stat().st_mtime)
            for old in files[:-COUNTRY_DEBUG_DUMP_KEEP]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    def _resolve(self, name: str, tag: str, profile, cancelled: Cancelled) -> CountryResult:
        key = make_key(name, tag)
        cached = self._cache.get(key)
        if cached:
            if cached.code:
                self._assets.ensure_flag(cached.code)
            return cached

        edge = self._get_edge()
        if not edge:
            return CountryResult(CountryStatus.ERROR, detail="Microsoft Edge not found")

        url = tracker_url(name, tag)
        wait_ms = COUNTRY_START_WAIT
        no_flag_hits = 0
        last_failure: Optional[CountryResult] = None

        for attempt in range(1, COUNTRY_ATTEMPTS + 1):
            if cancelled():
                return CountryResult(CountryStatus.INTERRUPTED, detail="Match ended before the search finished")

            try:
                html = run_edge(edge, url, profile, wait_ms)
            except Exception as e:
                last_failure = CountryResult(CountryStatus.ERROR, detail=str(e) or type(e).__name__)
                log.info("country %s#%s attempt %d: Edge error: %s", name, tag, attempt, last_failure.detail)
                self._sleep(3, cancelled)
                continue

            v = classify_page(html, name, tag)
            log.info("country %s#%s attempt %d (wait %dms): %s %s | title=%r len=%d",
                     name, tag, attempt, wait_ms, v.status.value, v.code or v.detail, html_title(html)[:60], len(html))

            if v.status is CountryStatus.FOUND:
                result = CountryResult(CountryStatus.FOUND, v.code)
                self._cache.put(key, result)
                self._assets.ensure_flag(v.code)
                return result

            if v.status is CountryStatus.PRIVATE:
                self._dump(v.status, name, html)
                result = CountryResult(CountryStatus.PRIVATE, detail=v.detail)
                self._cache.put(key, result)
                return result

            if v.status is CountryStatus.BLOCKED:
                # challenge не лечится ожиданием — просто новый запуск через паузу
                last_failure = CountryResult(CountryStatus.BLOCKED, detail=v.detail)
                wait_ms = COUNTRY_START_WAIT
                self._sleep(4, cancelled)
                continue

            if v.status is CountryStatus.BAD_PAGE:
                last_failure = CountryResult(CountryStatus.BAD_PAGE, detail=v.detail)
                self._dump(v.status, name, html)
                wait_ms += 3000
                self._sleep(3, cancelled)
                continue

            # NO_COUNTRY: профиль нужного игрока загружен, флага нет. Флаг мог просто не успеть подгрузиться
            # (так считал и старый алгоритм) — поэтому подтверждаем повторной загрузкой с бо́льшим бюджетом.
            no_flag_hits += 1
            if no_flag_hits == 1:
                self._dump(v.status, name, html)
            if no_flag_hits >= COUNTRY_NO_FLAG_CONFIRMATIONS:
                result = CountryResult(CountryStatus.NO_COUNTRY, detail="Profile is public but has no country set")
                self._cache.put(key, result)
                return result
            wait_ms += 5000
            self._sleep(2, cancelled)

        if no_flag_hits and last_failure is None:
            result = CountryResult(CountryStatus.NO_COUNTRY, detail="Profile is public but has no country set")
            self._cache.put(key, result)
            return result

        base = last_failure or CountryResult(CountryStatus.ERROR, detail="No attempts made")
        extra = " Profile loaded without a flag once, but it could not be confirmed." if no_flag_hits else ""
        return CountryResult(base.status, detail=f"{base.detail} (gave up after {COUNTRY_ATTEMPTS} attempts).{extra}")
