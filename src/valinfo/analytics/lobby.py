"""Лобби для аналитики: простые dataclass'ы, собираемые из live-данных воркера (без Qt)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from valinfo.modes import FFA, Mode, detect_layout, mode_for

ALLY, ENEMY, SOLO = "ally", "enemy", "ffa"


@dataclass
class Player:
    puuid: str
    name: str
    team: str                       # ally / enemy / ffa
    agent_uuid: str = ""
    agent: str = "—"
    role: str = "Unknown"
    tier: int = 0
    rr: int = 0
    peak_tier: int = 0
    records: list = field(default_factory=list)      # MatchRecord, новые первыми
    hidden: bool = False
    is_me: bool = False
    level: Optional[int] = None
    total_games: Optional[int] = None
    acts_played: Optional[int] = None


@dataclass
class Lobby:
    map_name: str
    map_url: str
    queue: str
    mode: Mode
    players: list
    me: str = ""
    layout: str = "teams"

    @property
    def ally(self) -> list:
        return [p for p in self.players if p.team == ALLY]

    @property
    def enemy(self) -> list:
        return [p for p in self.players if p.team == ENEMY]


def build_lobby(payload: dict, hidden_label: str = "Hidden") -> Lobby:
    """live-payload воркера → Lobby (только данные, никакой тяжёлой работы)."""
    mode = mode_for(payload.get("queue"))
    raw = payload["players"]
    me = payload["me"]
    layout = payload.get("layout") or detect_layout(mode, raw)
    my_team = next((p["team"] for p in raw if p["subject"] == me), raw[0]["team"] if raw else None)
    agents, roles, names = payload.get("agents") or {}, payload.get("roles") or {}, payload.get("names") or {}
    records, infos = payload.get("records") or {}, payload.get("info") or {}

    players = []
    for p in raw:
        s = p["subject"]
        ident = p.get("ident") or {}
        auuid = (p.get("agent") or "").lower()
        aname = agents.get(auuid, "—")
        is_me = s == me
        hidden = bool(ident.get("Incognito")) and not is_me
        nm = names.get(s)
        name = f"{aname} ({hidden_label})" if hidden else (f"{nm[0]}#{nm[1]}" if nm else "…")
        inf = infos.get(s) or {}
        team = SOLO if layout == FFA else (ALLY if p["team"] == my_team else ENEMY)
        level = None if (ident.get("HideAccountLevel") and not is_me) else ident.get("AccountLevel")
        players.append(Player(
            puuid=s, name=name, team=team, agent_uuid=auuid, agent=aname, role=roles.get(auuid, "Unknown"),
            tier=inf.get("cur_tier") or 0, rr=inf.get("rr") or 0, peak_tier=inf.get("peak_tier") or 0,
            records=list(records.get(s) or []), hidden=hidden, is_me=is_me, level=level,
            total_games=inf.get("total_games"), acts_played=inf.get("acts_played")))
    return Lobby(map_name=payload.get("map_name", ""), map_url=(payload.get("map_url") or "").lower(),
                 queue=payload.get("queue", ""), mode=mode, players=players, me=me, layout=layout)


def payload_signature(payload: dict, kind: str = "") -> tuple:
    """Хэшируемая подпись входных данных анализа: если она не изменилась, пересчитывать нечего."""
    infos = payload.get("info") or {}
    recs = payload.get("records") or {}
    done = payload.get("info_done")
    rows = []
    for p in payload["players"]:
        s = p["subject"]
        inf = infos.get(s) or {}
        rows.append((s, (p.get("agent") or "").lower(), inf.get("cur_tier"), inf.get("rr"), inf.get("peak_tier"),
                     len(recs.get(s) or ()), (s in done) if done is not None else True,
                     (payload.get("names") or {}).get(s) is not None))
    return (kind, payload.get("mid"), payload.get("queue"), payload.get("map_url"), tuple(rows))


def loading_progress(payload: dict) -> tuple:
    """(сколько игроков уже загружено, всего) — для индикатора на вкладках анализа."""
    done = payload.get("info_done")
    total = len(payload["players"])
    if done is None:
        return total, total
    return sum(1 for p in payload["players"] if p["subject"] in done), total
