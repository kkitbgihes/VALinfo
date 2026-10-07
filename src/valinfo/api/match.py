"""Текущий матч игрока (pregame / core-game) и имена участников."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LiveMatch:
    kind: str            # "pregame" | "ingame"
    mid: str
    players: list
    map_url: str
    queue: str = ""      # competitive / unrated / swiftplay / deathmatch / ... ("" — неизвестно)


def _pack(lst, team=None):
    return [dict(
        subject=p["Subject"],
        team=team or p["TeamID"],
        agent=p.get("CharacterID", ""),
        ident=p.get("PlayerIdentity") or {},
    ) for p in lst]


def get_match(riot, me):
    r = riot.req("GET", f"{riot.glz}/core-game/v1/players/{me}")
    if r.status_code == 200:
        mid = r.json()["MatchID"]
        m = riot.get(f"{riot.glz}/core-game/v1/matches/{mid}")
        if m:
            queue = ((m.get("MatchmakingData") or {}).get("QueueID") or "").lower()
            if not queue and m.get("ProvisioningFlow") == "CustomGame":
                queue = "custom"
            return LiveMatch("ingame", mid, _pack(m["Players"]), m.get("MapID", ""), queue)

    r = riot.req("GET", f"{riot.glz}/pregame/v1/players/{me}")
    if r.status_code == 200:
        mid = r.json()["MatchID"]
        m = riot.get(f"{riot.glz}/pregame/v1/matches/{mid}")
        if m:
            t = m["AllyTeam"]
            queue = (m.get("QueueID") or "").lower()
            if not queue and m.get("ProvisioningFlow") == "CustomGame":
                queue = "custom"
            return LiveMatch("pregame", mid, _pack(t["Players"], t["TeamID"]), m.get("MatchMap", ""), queue)

    return None


def fetch_names(riot, subjects, cache):
    need = [s for s in subjects if s not in cache]
    if not need:
        return
    r = riot.req("PUT", f"{riot.pd}/name-service/v2/players", json=need)
    if r.status_code == 200:
        for n in r.json():
            cache[n["Subject"]] = (n["GameName"], n["TagLine"])
