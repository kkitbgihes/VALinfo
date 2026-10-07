"""Единый разбор match-details.

Сырой ответ Riot весит сотни КБ. Раньше его хранили целиком и каждая функция (статистика, пати, пост-матч)
лезла в него заново. Теперь он разбирается РОВНО ОДИН РАЗ в компактный `MatchSummary`
(несколько КБ: записи сразу всех 10 игроков + id пати), а сам JSON выбрасывается.
Из этих записей считается всё: live-таблица, прогноз, анализ игроков, стороны карты, пати.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TRADE_WINDOW_MS = 5000      # убийство отомщено (traded), если враг убит союзником в течение 5 секунд

_TIME_KEYS = ("timeSinceGameStartMillis", "gameTime", "timeSinceRoundStartMillis", "roundTime")


@dataclass(slots=True)
class MatchRecord:
    """Один игрок в одном матче. Всё, что нужно аналитике, без сырых раундов."""
    mid: str
    started_ms: int
    queue: str
    map_url: str                 # mapUrl в нижнем регистре (как в StaticData.maps)
    agent: str                   # uuid агента, нижний регистр
    role: str
    tier: int                    # ранг игрока на момент матча (0 — нет данных)
    won: bool
    rounds: int
    kills: int
    deaths: int
    assists: int
    score: int
    damage: float
    hs: int
    bs: int
    ls: int
    fk: int = 0                  # первые убийства раунда
    fd: int = 0                  # первые смерти раунда
    kast: int = 0                # раунды с Kill / Assist / Survived / Traded
    mk3: int = 0                 # раунды с 3+ убийствами
    trade_k: int = 0             # убийства-«трейды» (отомстил за союзника)
    traded_d: int = 0            # смерти, за которые отомстили
    plants: int = 0
    defuses: int = 0
    atk_rounds: int = 0
    atk_won: int = 0
    atk_k: int = 0
    atk_d: int = 0
    atk_dmg: float = 0.0
    def_rounds: int = 0
    def_won: int = 0
    def_k: int = 0
    def_d: int = 0
    def_dmg: float = 0.0
    rw: int = 0                  # раундов выиграла команда игрока
    rl: int = 0                  # раундов проиграла
    lobby_acs: float = 0.0       # средний ACS всех игроков матча
    placement: int = 0           # место по ACS в матче (1 — лучший)
    lobby_tier: float = 0.0      # средний ранг лобби (по игрокам с рангом)
    m_atk_won: int = 0           # уровень матча: раундов выиграли атакующие / всего (для статистики карт)
    m_rounds: int = 0


@dataclass(slots=True)
class MatchSummary:
    mid: str
    started_ms: int
    queue: str
    map_url: str
    rounds: int
    atk_won: int
    records: dict = field(default_factory=dict)     # puuid -> MatchRecord
    parties: dict = field(default_factory=dict)     # puuid -> partyId


class _Acc:
    """Счётчики одного игрока по раундам (временный объект на время разбора)."""
    __slots__ = ("dmg", "hs", "bs", "ls", "fk", "fd", "kast", "mk3", "trade_k", "traded_d", "plants", "defuses",
                 "atk_rounds", "atk_won", "atk_k", "atk_d", "atk_dmg",
                 "def_rounds", "def_won", "def_k", "def_d", "def_dmg")

    def __init__(self):
        for n in self.__slots__:
            setattr(self, n, 0)


def _ktime(k: dict) -> float:
    for key in _TIME_KEYS:
        v = k.get(key)
        if v is not None:
            return v
    return 0


def _assistant_ids(k: dict) -> list:
    out = []
    for a in k.get("assistants") or []:
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, dict):
            v = a.get("assistantSubject") or a.get("subject") or a.get("assistantPuuid")
            if v:
                out.append(v)
    return out


def parse_match_summary(det: Optional[dict], roles: Optional[dict] = None) -> Optional[MatchSummary]:
    if not det or not det.get("players"):
        return None
    roles = roles or {}
    mi = det.get("matchInfo") or {}
    mid = mi.get("matchId") or ""
    rounds_res = det.get("roundResults") or []
    n_rounds = len(rounds_res)

    team_of = {p["subject"]: p.get("teamId") for p in det["players"]}
    team_ids = sorted({t for t in team_of.values() if t})
    teams = {t.get("teamId"): t for t in det.get("teams") or []}
    acc = {s: _Acc() for s in team_of}

    def other_team(t):
        if len(team_ids) != 2 or t not in team_ids:
            return None
        return team_ids[0] if team_ids[1] == t else team_ids[1]

    atk_total = 0
    for r in rounds_res:
        win_team = r.get("winningTeam")
        role = r.get("winningTeamRole")
        planter, defuser = r.get("bombPlanter"), r.get("bombDefuser")
        atk_team = None
        if role in ("Attacker", "Defender") and win_team:
            atk_team = win_team if role == "Attacker" else other_team(win_team)
        elif planter in team_of:
            atk_team = team_of[planter]
        elif defuser in team_of:
            atk_team = other_team(team_of[defuser])
        if atk_team is not None and atk_team == win_team:
            atk_total += 1

        if planter in acc:
            acc[planter].plants += 1
        if defuser in acc:
            acc[defuser].defuses += 1

        kills = []                       # (time, killer, victim, assistants)
        dmg_round = {}
        for ps in r.get("playerStats") or []:
            s = ps.get("subject")
            a = acc.get(s)
            if a is None:
                continue
            d_sum = 0
            for d in ps.get("damage") or []:
                dd = d.get("damage", 0)
                d_sum += dd
                a.hs += d.get("headshots", 0)
                a.bs += d.get("bodyshots", 0)
                a.ls += d.get("legshots", 0)
            a.dmg += d_sum
            dmg_round[s] = d_sum
            for k in ps.get("kills") or []:
                kills.append((_ktime(k), k.get("killer") or s, k.get("victim"), _assistant_ids(k)))

        kills.sort(key=lambda x: x[0])
        k_cnt, assisted, dead, traded, trader = {}, set(), set(), set(), []
        for i, (t, killer, victim, assists) in enumerate(kills):
            k_cnt[killer] = k_cnt.get(killer, 0) + 1
            dead.add(victim)
            assisted.update(assists)
            if i == 0:
                if killer in acc:
                    acc[killer].fk += 1
                if victim in acc:
                    acc[victim].fd += 1
            vt = team_of.get(victim)
            for t2, killer2, victim2, _ in kills[i + 1:]:
                if t2 - t > TRADE_WINDOW_MS:
                    break
                if victim2 == killer and killer2 != victim and team_of.get(killer2) == vt and vt is not None:
                    traded.add(victim)
                    trader.append(killer2)
                    break
        for tk in trader:
            if tk in acc:
                acc[tk].trade_k += 1

        for s, a in acc.items():
            if s not in dmg_round:
                continue                                  # игрока в этом раунде не было (вышел / нет данных)
            kc = k_cnt.get(s, 0)
            if kc or s in assisted or s not in dead or s in traded:
                a.kast += 1
            if kc >= 3:
                a.mk3 += 1
            if s in traded:
                a.traded_d += 1
            if atk_team is not None:
                side_atk = team_of[s] == atk_team
                won = team_of[s] == win_team
                if side_atk:
                    a.atk_rounds += 1; a.atk_won += won; a.atk_k += kc; a.atk_d += (s in dead); a.atk_dmg += dmg_round.get(s, 0)
                else:
                    a.def_rounds += 1; a.def_won += won; a.def_k += kc; a.def_d += (s in dead); a.def_dmg += dmg_round.get(s, 0)

    # ── уровень матча ──
    infos = []
    for p in det["players"]:
        st = p.get("stats") or {}
        rp = st.get("roundsPlayed") or n_rounds or 1
        infos.append((p["subject"], st.get("score", 0), rp))
    lobby_acs = sum(sc / max(rp, 1) for _, sc, rp in infos) / max(len(infos), 1)
    order = sorted(infos, key=lambda x: -x[1] / max(x[2], 1))
    placement = {s: i + 1 for i, (s, _, _) in enumerate(order)}
    tiers = [p.get("competitiveTier") or 0 for p in det["players"]]
    ranked = [t for t in tiers if t >= 3]
    lobby_tier = sum(ranked) / len(ranked) if ranked else 0.0

    started = mi.get("gameStartMillis") or 0
    queue = (mi.get("queueID") or "").lower()
    map_url = (mi.get("mapId") or "").lower()
    records, parties = {}, {}
    for p in det["players"]:
        s = p["subject"]
        st = p.get("stats") or {}
        a = acc[s]
        tm = teams.get(p.get("teamId")) or {}
        rw = tm.get("roundsWon", 0) or 0
        rl = max((n_rounds or 0) - rw, 0) if n_rounds else 0
        agent = (p.get("characterId") or "").lower()
        records[s] = MatchRecord(
            mid=mid, started_ms=started, queue=queue, map_url=map_url, agent=agent,
            role=roles.get(agent, "Unknown"), tier=p.get("competitiveTier") or 0, won=bool(tm.get("won", False)),
            rounds=st.get("roundsPlayed") or n_rounds, kills=st.get("kills", 0), deaths=st.get("deaths", 0),
            assists=st.get("assists", 0), score=st.get("score", 0), damage=a.dmg, hs=a.hs, bs=a.bs, ls=a.ls,
            fk=a.fk, fd=a.fd, kast=a.kast, mk3=a.mk3, trade_k=a.trade_k, traded_d=a.traded_d,
            plants=a.plants, defuses=a.defuses,
            atk_rounds=a.atk_rounds, atk_won=a.atk_won, atk_k=a.atk_k, atk_d=a.atk_d, atk_dmg=a.atk_dmg,
            def_rounds=a.def_rounds, def_won=a.def_won, def_k=a.def_k, def_d=a.def_d, def_dmg=a.def_dmg,
            rw=rw, rl=rl, lobby_acs=lobby_acs, placement=placement.get(s, 0), lobby_tier=lobby_tier,
            m_atk_won=atk_total, m_rounds=n_rounds,
        )
        if p.get("partyId"):
            parties[s] = p["partyId"]
    return MatchSummary(mid=mid, started_ms=started, queue=queue, map_url=map_url, rounds=n_rounds,
                        atk_won=atk_total, records=records, parties=parties)
