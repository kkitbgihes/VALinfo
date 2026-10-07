"""Синтетические данные для тестов: детали матча Riot, фейковый клиент, лобби."""
from __future__ import annotations

import random
import threading
import time

AGENTS = {  # uuid -> (имя, роль)
    "ag-duel1": ("Jett", "Duelist"), "ag-duel2": ("Reyna", "Duelist"), "ag-init1": ("Sova", "Initiator"),
    "ag-init2": ("Fade", "Initiator"), "ag-ctrl1": ("Omen", "Controller"), "ag-ctrl2": ("Brimstone", "Controller"),
    "ag-sent1": ("Cypher", "Sentinel"), "ag-sent2": ("Killjoy", "Sentinel"),
}
ROLES = {k: v[1] for k, v in AGENTS.items()}
NAMES = {k: v[0] for k, v in AGENTS.items()}
AGENT_LIST = list(AGENTS)


def make_details(mid, puuids, *, started=1_700_000_000_000, rounds=20, queue="competitive", map_id="/Game/Maps/Ascent",
                 strong=(), seed=0, ffa=False, tiers=None):
    """Матч: puuids[:5] — Blue, puuids[5:] — Red (или у каждого своя команда при ffa=True).
    Игроки из `strong` убивают заметно чаще."""
    rnd = random.Random(seed)
    n = len(puuids)
    team = {p: (p if ffa else ("Blue" if i < n // 2 else "Red")) for i, p in enumerate(puuids)}
    players = []
    kills_tot = {p: 0 for p in puuids}
    deaths_tot = {p: 0 for p in puuids}
    assists_tot = {p: 0 for p in puuids}
    dmg_tot = {p: 0 for p in puuids}
    results = []
    blue_wins = 0
    for r in range(rounds):
        alive = {p: True for p in puuids}
        stats = {p: {"subject": p, "kills": [], "damage": []} for p in puuids}
        t = 1000 * (r + 1) * 100
        for _ in range(rnd.randint(4, 9)):
            al = [p for p in puuids if alive[p]]
            if len(al) < 2:
                break
            w = [3.0 if p in strong else 1.0 for p in al]
            killer = rnd.choices(al, weights=w)[0]
            victims = [p for p in al if team[p] != team[killer]] if not ffa else [p for p in al if p != killer]
            if not victims:
                break
            victim = rnd.choice(victims)
            alive[victim] = False
            t += rnd.randint(500, 9000)
            mates = [p for p in al if team[p] == team[killer] and p != killer]
            ass = [{"assistantSubject": rnd.choice(mates)}] if mates and rnd.random() < 0.3 else []
            stats[killer]["kills"].append({"killer": killer, "victim": victim, "timeSinceRoundStartMillis": t,
                                           "timeSinceGameStartMillis": t, "assistants": ass})
            stats[killer]["damage"].append({"damage": 140, "headshots": 1 + (killer in strong), "bodyshots": 1, "legshots": 0})
            kills_tot[killer] += 1
            deaths_tot[victim] += 1
            for a in ass:
                assists_tot[a["assistantSubject"]] += 1
            dmg_tot[killer] += 140
        blue_alive = sum(alive[p] for p in puuids if team[p] == "Blue")
        red_alive = sum(alive[p] for p in puuids if team[p] == "Red")
        winner = "Blue" if (blue_alive >= red_alive) else "Red"
        if ffa:
            winner = puuids[0]
        blue_wins += winner == "Blue"
        # Blue атакует в первых 12 раундах (или 1-2 половины)
        atk = "Blue" if r < 12 else "Red"
        results.append({"roundNum": r, "winningTeam": winner, "winningTeamRole": "Attacker" if winner == atk else "Defender",
                        "roundResultCode": "Elimination", "plantSite": "", "playerStats": list(stats.values())})
    for i, p in enumerate(puuids):
        agent = AGENT_LIST[i % len(AGENT_LIST)]
        players.append({"subject": p, "teamId": team[p], "characterId": agent, "competitiveTier": (tiers or {}).get(p, 12),
                        "partyId": f"party-{i // 2}", "gameName": f"N{i}", "tagLine": "T",
                        "stats": {"kills": kills_tot[p], "deaths": deaths_tot[p], "assists": assists_tot[p],
                                  "score": kills_tot[p] * 150 + dmg_tot[p] // 3, "roundsPlayed": rounds}})
    teams = [{"teamId": "Blue", "won": blue_wins * 2 > rounds, "roundsWon": blue_wins},
             {"teamId": "Red", "won": blue_wins * 2 <= rounds, "roundsWon": rounds - blue_wins}]
    if ffa:
        teams = [{"teamId": p, "won": p == puuids[0], "roundsWon": 0} for p in puuids]
    return {"matchInfo": {"matchId": mid, "mapId": map_id, "queueID": queue, "gameStartMillis": started},
            "players": players, "teams": teams, "roundResults": results}


class FakeRiot:
    """Фейковый клиент Riot: считает обращения, выдаёт историю и детали матчей."""
    pd = "https://pd"
    glz = "https://glz"

    def __init__(self, details: dict, history: dict, mmr=None, delay=0.0):
        self.details = details              # mid -> детали
        self.history = history              # puuid -> [mid, ...]
        self.mmr = mmr or {}
        self.delay = delay
        self.calls = {"details": 0, "history": 0, "mmr": 0}
        self._lock = threading.Lock()

    def get(self, url, params=None, **kw):
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            if "/match-details/" in url:
                self.calls["details"] += 1
            elif "/history/" in url:
                self.calls["history"] += 1
            elif "/mmr/" in url:
                self.calls["mmr"] += 1
        if "/mmr/" in url:
            return self.mmr.get(url.rsplit("/", 1)[1]) or {
                "LatestCompetitiveUpdate": {"TierAfterUpdate": 12, "RankedRatingAfterUpdate": 30, "SeasonID": "s1",
                                            "MatchStartTime": 1_700_000_000_000},
                "QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {
                    "S1": {"NumberOfGames": 40, "WinsByTier": {"15": 3, "9": 1}}}}}}
        if "/history/" in url:
            puuid = url.rsplit("/", 1)[1]
            p = params or {}
            start, end = p.get("startIndex", 0), p.get("endIndex", 20)
            mids = self.history.get(puuid, [])[start:end]
            return {"History": [{"MatchID": m, "GameStartTime": self.details[m]["matchInfo"]["gameStartMillis"],
                                 "QueueID": self.details[m]["matchInfo"]["queueID"]} for m in mids]}
        return self.details.get(url.rsplit("/", 1)[1])
