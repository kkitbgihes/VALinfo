"""Все пути в одном месте. Директории создаются явно через `ensure_dirs()`, а не при импорте."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _local_appdata() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    return Path(value) if value else Path.home() / ".local" / "share"


LOCAL_APPDATA = _local_appdata()

CACHE_DIR = LOCAL_APPDATA / "ValInfoCache"

AGENTS_DIR = CACHE_DIR / "agents"
RANKS_DIR = CACHE_DIR / "ranks"
FLAGS_DIR = CACHE_DIR / "flags"
EDGE_PROFILES_DIR = CACHE_DIR / "edge_profiles"
DEBUG_PAGES_DIR = CACHE_DIR / "debug_pages"
LOG_DIR = CACHE_DIR / "logs"

COUNTRY_CACHE_FILE = CACHE_DIR / "countries.json"
SETTINGS_FILE = CACHE_DIR / "settings.json"
# Сюда можно положить свой <код>.json с переводом — он подхватится без пересборки приложения
USER_LOCALES_DIR = CACHE_DIR / "locales"
STATIC_CACHE_FILE = CACHE_DIR / "static.json"

LOCKFILE = LOCAL_APPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"
LOGFILE = LOCAL_APPDATA / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log"


def ensure_dirs() -> None:
    for d in (CACHE_DIR, AGENTS_DIR, RANKS_DIR, FLAGS_DIR, EDGE_PROFILES_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resource_path(relative: str) -> str:
    """Путь к ресурсу и из исходников, и из PyInstaller-сборки."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = Path(__file__).resolve().parents[2]  # корень проекта (рядом с папкой assets)
    return str(Path(base) / relative)
