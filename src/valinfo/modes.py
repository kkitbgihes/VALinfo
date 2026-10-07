"""Игровые режимы: как рисовать лобби, что для него считать и какая история нужна.

Чтобы поддержать новый режим, достаточно добавить строку в `MODES` (+ перевод `mode.<key>` в locales).
Неизвестные очереди не ломают трекер: они ведут себя как обычный командный матч.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TEAMS = "teams"      # две (или больше) команды: две таблицы
FFA = "ffa"          # каждый сам за себя: ОДНА общая таблица

BOMB_13 = "bomb13"   # 13 раундов, смена сторон на 12-м, овертайм до +2
SWIFT_5 = "swift5"   # Swiftplay: до 5 раундов, смена сторон после 4-го, решающий раунд при 4:4

COMP_ONLY = ("competitive",)


@dataclass(frozen=True)
class Mode:
    queue: str
    key: str                         # суффикс ключа перевода: mode.<key>
    layout: str = TEAMS
    ruleset: Optional[str] = None    # None = прогноз исхода для режима не строим
    history: tuple = COMP_ONLY       # из каких очередей брать историю игроков (по порядку, с добором)


MODES: dict[str, Mode] = {m.queue: m for m in (
    Mode("competitive", "competitive", TEAMS, BOMB_13, ("competitive",)),
    Mode("unrated", "unrated", TEAMS, BOMB_13, ("unrated", "competitive")),
    Mode("swiftplay", "swiftplay", TEAMS, SWIFT_5, ("swiftplay", "competitive")),
    Mode("premier-seasonmatch", "premier", TEAMS, BOMB_13, ("competitive",)),
    Mode("spikerush", "spikerush", TEAMS, None, ("spikerush", "competitive")),
    Mode("deathmatch", "deathmatch", FFA, None, COMP_ONLY),          # 10 независимых игроков
    Mode("ggteam", "escalation", TEAMS, None, COMP_ONLY),
    Mode("hurm", "tdm", TEAMS, None, COMP_ONLY),
    Mode("onefa", "replication", TEAMS, None, COMP_ONLY),
    Mode("snowball", "snowball", TEAMS, None, COMP_ONLY),
    Mode("newmap", "newmap", TEAMS, None, COMP_ONLY),
    Mode("custom", "custom", TEAMS, None, COMP_ONLY),
)}
UNKNOWN = Mode("", "unknown", TEAMS, None, COMP_ONLY)


def mode_for(queue_id: Optional[str]) -> Mode:
    q = (queue_id or "").lower()
    if q in MODES:
        return MODES[q]
    if q.startswith("premier"):
        return MODES["premier-seasonmatch"]
    return Mode(q, "unknown", TEAMS, None, COMP_ONLY) if q else UNKNOWN


def detect_layout(mode: Mode, players: list) -> str:
    """FFA — если так сказано в режиме ИЛИ если у каждого игрока своя «команда» (бой насмерть)."""
    if mode.layout == FFA:
        return FFA
    teams = {p.get("team") for p in players}
    if len(players) > 2 and len(teams) == len(players):
        return FFA
    return TEAMS


def ruleset_of(queue_id: Optional[str]) -> Optional[str]:
    return mode_for(queue_id).ruleset
