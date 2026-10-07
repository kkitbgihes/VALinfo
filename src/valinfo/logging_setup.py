"""Логи в файл (у windowed-сборки нет консоли). Ротация: 2 × 512 КБ, на диск пишется только INFO и выше."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from valinfo.paths import LOG_DIR


def setup_logging() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_DIR / "valinfo.log", maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
    except OSError:
        pass
