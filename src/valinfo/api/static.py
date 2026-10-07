"""Справочные данные valorant-api.com (агенты, карты, сезоны, ранги) с дисковым кэшем.

Версия клиента запрашивается ВСЕГДА (она нужна для заголовков и меняется с патчами),
остальное живёт на диске STATIC_CACHE_TTL_HOURS — запуск без сети-ожидания.
Если сети нет, но кэш есть (даже протухший) — стартуем с кэшем.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import requests

from valinfo.config import PEAK_FROM, STATIC_CACHE_TTL_HOURS
from valinfo.paths import STATIC_CACHE_FILE

log = logging.getLogger(__name__)
BASE = "https://valorant-api.com/v1/"


@dataclass
class StaticData:
    version: str
    agents: dict            # uuid -> name
    seasons: set            # uuid акта, учитываемые для пика
    maps: dict              # mapUrl.lower() -> name
    agent_icons: dict = field(default_factory=dict)   # uuid -> url
    tier_icons: dict = field(default_factory=dict)    # tier(int) -> url
    agent_roles: dict = field(default_factory=dict)   # uuid -> Duelist / Initiator / Controller / Sentinel
    season_dates: dict = field(default_factory=dict)  # uuid акта -> ISO-дата начала (для даты пика)


def _fetch(path: str):
    r = requests.get(BASE + path, timeout=10)
    r.raise_for_status()
    return r.json()["data"]


def _read_cache() -> Optional[dict]:
    try:
        with open(STATIC_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        ok = isinstance(data, dict) and "agents" in data and "agent_roles" in data and "season_dates" in data
        return data if ok else None
    except Exception:
        return None


def _write_cache(sd: StaticData) -> None:
    payload = {
        "ts": time.time(), "version": sd.version, "agents": sd.agents,
        "seasons": sorted(sd.seasons), "maps": sd.maps,
        "agent_icons": sd.agent_icons, "tier_icons": {str(k): v for k, v in sd.tier_icons.items()},
        "agent_roles": sd.agent_roles, "season_dates": sd.season_dates,
    }
    tmp = f"{STATIC_CACHE_FILE}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, STATIC_CACHE_FILE)
    except OSError:
        pass


def _from_cache(c: dict, version: str) -> StaticData:
    return StaticData(
        version=version, agents=c["agents"], seasons=set(c.get("seasons") or []), maps=c.get("maps") or {},
        agent_icons=c.get("agent_icons") or {},
        tier_icons={int(k): v for k, v in (c.get("tier_icons") or {}).items()},
        agent_roles=c.get("agent_roles") or {}, season_dates=c.get("season_dates") or {},
    )


def load_static() -> StaticData:
    cached = _read_cache()
    fresh = bool(cached) and (time.time() - cached.get("ts", 0)) < STATIC_CACHE_TTL_HOURS * 3600

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_ver = ex.submit(_fetch, "version")
        futs = {}
        if not fresh:
            futs = {k: ex.submit(_fetch, p) for k, p in (
                ("agents", "agents?isPlayableCharacter=true"), ("seasons", "seasons"),
                ("maps", "maps"), ("tiers", "competitivetiers"))}

        try:
            version = f_ver.result()["riotClientVersion"]
        except Exception:
            if not cached:
                raise
            version = cached.get("version", "")
            log.warning("version request failed, using cached %s", version)

        if fresh:
            return _from_cache(cached, version)

        try:
            agents_raw = futs["agents"].result()
            seasons_raw = futs["seasons"].result()
            maps_raw = futs["maps"].result()
            tiers_raw = futs["tiers"].result()[-1]["tiers"]
        except Exception:
            if cached:
                log.warning("static refresh failed, using stale cache", exc_info=True)
                return _from_cache(cached, version)
            raise

    sd = StaticData(
        version=version,
        agents={a["uuid"].lower(): a["displayName"] for a in agents_raw},
        seasons={
            s["uuid"].lower() for s in seasons_raw
            if s.get("type") == "EAresSeasonType::Act" and (s.get("startTime") or "") >= PEAK_FROM
        },
        maps={m["mapUrl"].lower(): m["displayName"] for m in maps_raw},
        agent_icons={a["uuid"].lower(): a.get("displayIcon") for a in agents_raw},
        tier_icons={t["tier"]: t.get("smallIcon") for t in tiers_raw},
        agent_roles={a["uuid"].lower(): (a.get("role") or {}).get("displayName", "Unknown") for a in agents_raw},
        season_dates={
            s["uuid"].lower(): s["startTime"] for s in seasons_raw
            if s.get("type") == "EAresSeasonType::Act" and s.get("startTime")
        },
    )
    _write_cache(sd)
    return sd
