"""Полный разбор завершённого матча (сводка, дуэли, экономика, раунды)."""
from __future__ import annotations

from collections import defaultdict


def parse_ultra_post_match(det, names_cache, agents_cache, me_puuid):
    if not det or "matchInfo" not in det:
        return None

    match_info = det.get("matchInfo", {})
    rounds_played = len(det.get("roundResults") or [])
    
    teams_summary = {}
    for t in det.get("teams") or []:
        teams_summary[t["teamId"]] = {
            "won": t.get("won", False),
            "rounds_won": t.get("roundsWon", 0),
            "num_points": t.get("numPoints", 0)
        }

    players_stats = {}
    player_team_map = {}
    for p in det.get("players") or []:
        s = p["subject"]
        st = p.get("stats") or {}
        ab = st.get("abilityCasts") or {}
        player_team_map[s] = p.get("teamId")
        players_stats[s] = {
            "puuid": s,
            "team": p.get("teamId"),
            "agent_uuid": p.get("characterId", "").lower(),
            "agent_name": agents_cache.get(p.get("characterId", "").lower(), "—"),
            "name": names_cache.get(s, (p.get("gameName", "Player"), p.get("tagLine", ""))),
            "kills": st.get("kills", 0),
            "deaths": st.get("deaths", 0),
            "assists": st.get("assists", 0),
            "score": st.get("score", 0),
            "acs": round(st.get("score", 0) / max(rounds_played, 1)),
            "damage": 0,
            "hs": 0, "bs": 0, "ls": 0,
            "fk": 0, "fd": 0,
            "plants": 0, "defuses": 0,
            "3k": 0, "4k": 0, "5k": 0,
            "spent_credits": 0,
            "ability_casts": {
                "c": ab.get("grenadeCasts") or 0,
                "q": ab.get("ability1Casts") or 0,
                "e": ab.get("ability2Casts") or 0,
                "x": ab.get("ultimateCasts") or 0,
            }
        }

    duels_matrix = defaultdict(lambda: defaultdict(int))
    round_timeline = []

    for r_idx, r in enumerate(det.get("roundResults") or []):
        winning_team = r.get("winningTeam")
        bomb_planter = r.get("bombPlanter")
        bomb_defuser = r.get("bombDefuser")
        plant_site = r.get("plantSite", "")

        if bomb_planter and bomb_planter in players_stats:
            players_stats[bomb_planter]["plants"] += 1
        if bomb_defuser and bomb_defuser in players_stats:
            players_stats[bomb_defuser]["defuses"] += 1

        round_kills = []
        for p_stat in r.get("playerStats") or []:
            s = p_stat.get("subject")
            
            econ = p_stat.get("economy") or {}
            if s in players_stats:
                players_stats[s]["spent_credits"] += econ.get("spent", 0)

            if s in players_stats:
                for d in p_stat.get("damage") or []:
                    players_stats[s]["damage"] += d.get("damage", 0)
                    players_stats[s]["hs"] += d.get("headshots", 0)
                    players_stats[s]["bs"] += d.get("bodyshots", 0)
                    players_stats[s]["ls"] += d.get("legshots", 0)

            for k in p_stat.get("kills") or []:
                round_kills.append({
                    "time": k.get("timeSinceGameStartMillis", 0),
                    "killer": s,
                    "victim": k.get("victim"),
                    "weapon": k.get("finishingDamage", {}).get("damageItem", "")
                })

        round_kills.sort(key=lambda x: x["time"])

        if round_kills:
            first_k = round_kills[0]
            if first_k["killer"] in players_stats: players_stats[first_k["killer"]]["fk"] += 1
            if first_k["victim"] in players_stats: players_stats[first_k["victim"]]["fd"] += 1

            for k in round_kills:
                if k["killer"] and k["victim"]:
                    duels_matrix[k["killer"]][k["victim"]] += 1

        for p_stat in r.get("playerStats") or []:
            s = p_stat.get("subject")
            k_count = len(p_stat.get("kills") or [])
            if s in players_stats:
                if k_count == 3: players_stats[s]["3k"] += 1
                elif k_count == 4: players_stats[s]["4k"] += 1
                elif k_count >= 5: players_stats[s]["5k"] += 1

        round_timeline.append({
            "round_num": r_idx + 1,
            "winning_team": winning_team,
            "win_result": r.get("roundResultCode", "Elimination"),
            "plant_site": plant_site,
            "planter": names_cache.get(bomb_planter, ("—", ""))[0] if bomb_planter else "—",
            "defuser": names_cache.get(bomb_defuser, ("—", ""))[0] if bomb_defuser else "—",
            "kills_count": len(round_kills)
        })

    for s, p in players_stats.items():
        p["adr"] = round(p["damage"] / max(rounds_played, 1))
        p["avg_spent"] = round(p["spent_credits"] / max(rounds_played, 1))
        total_shots = p["hs"] + p["bs"] + p["ls"]
        p["hs_percent"] = round((p["hs"] / max(total_shots, 1)) * 100, 1)

    sorted_players = sorted(players_stats.values(), key=lambda x: x["score"], reverse=True)
    if sorted_players:
        sorted_players[0]["mvp"] = "MATCH MVP"

    duels_dict = {k: dict(v) for k, v in duels_matrix.items()}

    return {
        "map_id": match_info.get("mapId", ""),
        "mode": match_info.get("queueID", "Default"),
        "game_length_millis": match_info.get("gameLengthMillis", 0),
        "rounds_played": rounds_played,
        "teams": teams_summary,
        "players": sorted_players,
        "duels": duels_dict,
        "timeline": round_timeline,
        "me": me_puuid
    }
