"""Файловый кэш картинок (иконки агентов/рангов, флаги). Никакого Qt — можно вызывать из любых потоков."""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from valinfo.api.http import make_session
from valinfo.paths import AGENTS_DIR, FLAGS_DIR, RANKS_DIR

log = logging.getLogger(__name__)


class AssetStore:
    def __init__(self):
        self._http = make_session()
        self._flag_lock = threading.Lock()

    # ── пути ──
    @staticmethod
    def agent_path(agent_uuid: str) -> Path:
        return AGENTS_DIR / f"{agent_uuid}.png"

    @staticmethod
    def rank_path(tier_id) -> Path:
        return RANKS_DIR / f"{tier_id}.png"

    @staticmethod
    def flag_path(code: str) -> Path:
        return FLAGS_DIR / f"{code.lower()}.png"

    def has_flag(self, code: str) -> bool:
        return self.flag_path(code).exists()

    # ── загрузка ──
    def _download(self, url: str, path: Path) -> None:
        try:
            r = self._http.get(url, timeout=5)
            if r.status_code == 200 and r.content:
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(r.content)
                os.replace(tmp, path)
        except Exception:
            log.debug("download failed: %s", url, exc_info=True)

    def preload(self, agent_icons: dict, tier_icons: dict) -> None:
        """Докачивает только недостающее (на тёплом кэше — десяток os.path.exists и всё)."""
        tasks = [(url, self.agent_path(u)) for u, url in agent_icons.items()
                 if url and not self.agent_path(u).exists()]
        tasks += [(url, self.rank_path(t)) for t, url in tier_icons.items()
                  if url and not self.rank_path(t).exists()]
        if tasks:
            with ThreadPoolExecutor(max_workers=10) as ex:
                list(ex.map(lambda t: self._download(*t), tasks))

    def ensure_flag(self, code: str) -> bool:
        """Скачивает PNG флага в кэш. True, если файл есть."""
        path = self.flag_path(code)
        if path.exists():
            return True
        with self._flag_lock:  # два воркера не качают один и тот же флаг
            if not path.exists():
                self._download(f"https://flagcdn.com/w80/{code.lower()}.png", path)
        return path.exists()
