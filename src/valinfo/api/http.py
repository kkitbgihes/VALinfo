"""Общая фабрика HTTP-сессий с увеличенным пулом соединений."""
from __future__ import annotations

import requests
import urllib3
from requests.adapters import HTTPAdapter

from valinfo.config import HTTP_POOL_SIZE

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def make_session() -> requests.Session:
    """Session с пулом под наши пулы потоков (иначе urllib3 сыпет 'Connection pool is full' и пересоздаёт TLS)."""
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=HTTP_POOL_SIZE)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s
