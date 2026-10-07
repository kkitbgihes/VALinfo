"""Запуск headless Microsoft Edge и получение DOM страницы (`--dump-dom`).

Механизм не менялся: тот же UA, тот же `--virtual-time-budget`, тот же отдельный профиль на слот.
Добавлены только «облегчающие» флаги (см. LIGHT_FLAGS), которые не влияют на рендер страницы.
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import threading
from typing import Optional

from valinfo.config import COUNTRY_EDGE_TIMEOUT, COUNTRY_LIGHT_EDGE_FLAGS

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

# Всё это отключает фоновую «жизнь» браузера (расширения, синхронизация, апдейты, телеметрию),
# но не трогает ни GPU/WebGL-отпечаток, ни сетевой стек страницы, ни JS — Cloudflare видит то же самое.
LIGHT_FLAGS = [
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--metrics-recording-only",
    "--no-default-browser-check",
]

_PROCS: set = set()
_PROCS_LOCK = threading.Lock()


def find_edge() -> Optional[str]:
    candidates = [shutil.which("msedge"), shutil.which("microsoft-edge")]
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _kill_tree(proc) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=0x08000000,
            )
        else:
            proc.kill()
    except Exception:
        pass


def kill_all_edges() -> None:
    with _PROCS_LOCK:
        procs = list(_PROCS)
    for p in procs:
        if p.poll() is None:
            _kill_tree(p)


atexit.register(kill_all_edges)


def run_edge(edge: str, url: str, profile_dir, wait_ms: int) -> str:
    os.makedirs(profile_dir, exist_ok=True)
    cmd = [
        edge,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--mute-audio",
        "--hide-scrollbars",
        "--lang=ru-RU",
        "--window-size=1280,900",
        "--user-data-dir=" + str(profile_dir),
        "--user-agent=" + USER_AGENT,
    ]
    if COUNTRY_LIGHT_EDGE_FLAGS:
        cmd += LIGHT_FLAGS
    cmd += ["--virtual-time-budget=%d" % wait_ms, "--dump-dom", url]

    kwargs = {}
    if os.name == "nt":
        # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS — чтобы Edge не мешал игре
        kwargs["creationflags"] = 0x08000000 | 0x00004000

    # stdin=DEVNULL обязателен: в сборке --windowed у процесса нет валидного stdin
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs
    )
    with _PROCS_LOCK:
        _PROCS.add(proc)
    try:
        out, _ = proc.communicate(timeout=COUNTRY_EDGE_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise RuntimeError(f"Edge timed out ({COUNTRY_EDGE_TIMEOUT}s)")
    finally:
        if proc.poll() is None:
            _kill_tree(proc)
        with _PROCS_LOCK:
            _PROCS.discard(proc)
    return out.decode("utf-8", "replace")
