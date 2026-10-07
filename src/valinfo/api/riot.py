"""Клиенты: локальный Riot Client (lockfile) и удалённое Riot API."""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Optional

import requests

from valinfo.api.http import make_session
from valinfo.constants import SHARDS
from valinfo.paths import LOCKFILE, LOGFILE

PLATFORM = base64.b64encode(json.dumps({
    "platformType": "PC",
    "platformOS": "Windows",
    "platformOSVersion": "10.0.19042.1.256.64bit",
    "platformChipset": "Unknown",
}).encode()).decode()


def read_lockfile() -> str:
    """Сырое содержимое lockfile. FileNotFoundError, если Riot Client не запущен."""
    return LOCKFILE.read_text().strip()


class Local:
    """Локальный API Riot Client. Сессия переиспользуется между тиками (пока lockfile не изменился)."""

    def __init__(self, raw: Optional[str] = None):
        self.raw = raw if raw is not None else read_lockfile()
        _, _, port, password, proto = self.raw.split(":")
        self.base, self.auth = f"{proto}://127.0.0.1:{port}", ("riot", password)
        self._s = make_session()

    def get(self, path):
        r = self._s.get(self.base + path, auth=self.auth, verify=False, timeout=5)
        r.raise_for_status()
        return r.json()


class Riot:
    def __init__(self, region, shard, version):
        self.glz = f"https://glz-{region}-1.{shard}.a.pvp.net"
        self.pd = f"https://pd.{shard}.a.pvp.net"
        self.s = make_session()
        self.h = {"X-Riot-ClientPlatform": PLATFORM, "X-Riot-ClientVersion": version}

    def set_tokens(self, t):
        self.h["Authorization"] = f"Bearer {t['accessToken']}"
        self.h["X-Riot-Entitlements-JWT"] = t["token"]

    def req(self, method, url, **kw):
        for _ in range(4):
            r = self.s.request(method, url, headers=self.h, timeout=10, **kw)
            if r.status_code != 429:
                return r
            time.sleep(float(r.headers.get("Retry-After", 2)))
        return r

    def get(self, url, **kw):
        try:
            r = self.req("GET", url, **kw)
            return r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None


def detect_region(local):
    try:
        for s in local.get("/product-session/v1/external-sessions").values():
            if s.get("productId") == "valorant":
                for a in s.get("launchConfiguration", {}).get("arguments", []):
                    if a.startswith("-ares-deployment="):
                        reg = a.split("=", 1)[1]
                        return reg, SHARDS.get(reg, reg)
    except Exception:
        pass
    try:
        log = LOGFILE.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"https://glz-([a-z]+)-1\.([a-z]+)\.a\.pvp\.net", log)
        if m:
            return m.group(1), m.group(2)
    except OSError:
        pass
    return "eu", "eu"
