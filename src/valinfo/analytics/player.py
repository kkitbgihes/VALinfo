"""Данные одного игрока для live-таблицы: ранг/пик из MMR + статистика из записей матчей.

Раньше здесь скачивались и разбирались match-details отдельно для таблицы. Теперь функция лишь берёт общий
`MatchStore` (каждый матч разбирается один раз на всё приложение) и сворачивает записи в числа таблицы.
Сами записи тоже возвращаются — на них строятся прогноз и анализ игроков.
"""
from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from valinfo.config import HISTORY_MIN_PRIMARY
from valinfo.modes import Mode


def _fmt_month(iso_or_ms) -> Optional[str]:
    """'2026-10-01T…' или мс-таймстемп → '10.2026'."""
    try:
        if isinstance(iso_or_ms, (int, float)):
            t = time.gmtime(iso_or_ms / 1000)
            return f"{t.tm_mon:02d}.{t.tm_year}"
        txt = str(iso_or_ms)
        return f"{int(txt[5:7]):02d}.{int(txt[:4])}"
    except (ValueError, TypeError, IndexError):
        return None


def parse_rank_info(mmr: Optional[dict], seasons: set, season_dates: dict) -> dict:
    """Ранг, пик (+ дата пика) и «опыт» аккаунта из одного ответа MMR."""
    data = {"cur_tier": 0, "rr": 0, "peak_tier": 0, "peak_date": None, "total_games": None, "acts_played": None}
    if not mmr:
        return data
    lu = mmr.get("LatestCompetitiveUpdate") or {}
    cur = lu.get("TierAfterUpdate") or 0
    data["cur_tier"] = cur
    data["rr"] = lu.get("RankedRatingAfterUpdate") or 0

    comp = (mmr.get("QueueSkills") or {}).get("competitive") or {}
    by_season = comp.get("SeasonalInfoBySeasonID") or {}
    peak, peak_sid = 0, None
    total_games, acts = 0, 0
    for sid, s in by_season.items():
        s = s or {}
        games = s.get("NumberOfGames") or 0
        total_games += games
        acts += 1 if games else 0
        if sid.lower() in seasons:
            for tier, wins in (s.get("WinsByTier") or {}).items():
                if not wins:
                    continue
                t = int(tier)
                # при равном пике берём более свежий акт
                if t > peak or (t == peak and season_dates.get(sid.lower(), "") > season_dates.get((peak_sid or "").lower(), "")):
                    peak, peak_sid = t, sid
    if by_season:
        data["total_games"], data["acts_played"] = total_games, acts

    data["peak_tier"] = max(peak, cur)
    # Дата пика — начало акта, в котором он достигнут (Riot хранит рейтинг по актам, точной даты матча нет).
    sid = peak_sid if peak and peak >= cur else (lu.get("SeasonID") if cur else None)
    if sid:
        data["peak_date"] = _fmt_month(season_dates.get(sid.lower()))
    return data


def table_stats(records: list, agent_names: dict) -> dict:
    """Числа live-таблицы из записей (новые матчи первыми). Формулы те же, что и раньше."""
    out = {"kd": None, "hs": None, "adr": None, "acs": None, "winrate": None, "streak": None,
           "main_agent_uuid": None, "main_agent": None, "analyzed_count": len(records)}
    if not records:
        return out
    k = sum(r.kills for r in records)
    d = sum(r.deaths for r in records)
    rounds = sum(r.rounds for r in records)
    shots = sum(r.hs + r.bs + r.ls for r in records)
    wins = sum(1 for r in records if r.won)
    out["kd"] = k / max(d, 1)
    out["hs"] = sum(r.hs for r in records) / max(shots, 1) * 100
    out["adr"] = sum(r.damage for r in records) / max(rounds, 1)
    out["acs"] = sum(r.score for r in records) / max(rounds, 1)
    out["winrate"] = wins / len(records) * 100

    first = records[0].won
    streak = 1
    for r in records[1:]:
        if r.won == first:
            streak += 1
        else:
            break
    out["streak"] = f"+{streak}W" if first else f"-{streak}L"

    agents = [r.agent for r in records if r.agent]
    if agents:
        top = Counter(agents).most_common(1)[0][0]
        out["main_agent_uuid"] = top
        out["main_agent"] = agent_names.get(top, "—")
    return out


def fetch_player(riot, puuid: str, *, seasons: set, season_dates: dict, agent_names: dict, mode: Mode,
                 count: int, matches, history, pool: ThreadPoolExecutor, on_partial=None):
    """→ (info для таблицы, список записей игрока, новые первыми).

    Порядок: MMR (1 запрос) → отдаём ранг сразу (`on_partial`) → список матчей → детали матчей через общий пул.
    """
    info = parse_rank_info(riot.get(f"{riot.pd}/mmr/v1/players/{puuid}"), seasons, season_dates)
    info.update(table_stats([], agent_names))
    if on_partial:
        on_partial(dict(info))

    records: list = []
    seen: set = set()
    for i, queue in enumerate(mode.history):
        if i > 0 and len(records) >= HISTORY_MIN_PRIMARY:
            break                                   # из «родной» очереди набралось достаточно — не добираем
        need = count - len(records)
        if need <= 0:
            break
        entries = [e for e in history.entries(riot, puuid, queue, need) if e.mid not in seen]
        seen.update(e.mid for e in entries)
        for summary in matches.get_many(riot, [e.mid for e in entries], pool):
            rec = summary.records.get(puuid) if summary else None
            if rec is not None:
                records.append(rec)
    records.sort(key=lambda r: r.started_ms, reverse=True)
    records = records[:count]
    info.update(table_stats(records, agent_names))
    return info, records
