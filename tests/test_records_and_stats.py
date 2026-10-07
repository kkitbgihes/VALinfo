import unittest

from valinfo.analytics.matchstore import HistoryStore, MatchStore
from valinfo.analytics.player import fetch_player, parse_rank_info, table_stats
from valinfo.analytics.records import parse_match_summary
from valinfo.modes import mode_for
from concurrent.futures import ThreadPoolExecutor
from helpers import AGENTS, FakeRiot, NAMES, ROLES, make_details

P = [f"p{i}" for i in range(10)]


class ParseSummaryTests(unittest.TestCase):
    def setUp(self):
        self.det = make_details("m1", P, strong=("p0",), seed=3)
        self.s = parse_match_summary(self.det, ROLES)

    def test_basic_fields(self):
        r = self.s.records["p0"]
        self.assertEqual(r.rounds, 20)
        self.assertEqual(r.queue, "competitive")
        self.assertEqual(r.map_url, "/game/maps/ascent")
        self.assertEqual(r.role, "Duelist")
        self.assertEqual(self.s.parties["p0"], "party-0")

    def test_kills_match_round_sums(self):
        kills = {p: 0 for p in P}
        for rd in self.det["roundResults"]:
            for ps in rd["playerStats"]:
                kills[ps["subject"]] += len(ps["kills"])
        # агрегаты из stats и сумма по раундам совпадают, а fk — не больше числа раундов
        for p in P:
            self.assertEqual(self.s.records[p].kills, kills[p])
            self.assertLessEqual(self.s.records[p].fk, 20)
        self.assertEqual(sum(r.fk for r in self.s.records.values()), sum(r.fd for r in self.s.records.values()))

    def test_side_split_covers_all_rounds(self):
        for r in self.s.records.values():
            self.assertEqual(r.atk_rounds + r.def_rounds, 20)
        self.assertEqual(self.s.records["p0"].atk_rounds, 12)        # Blue атакует первые 12 раундов
        self.assertEqual(self.s.records["p9"].atk_rounds, 8)

    def test_kast_between_zero_and_rounds(self):
        for r in self.s.records.values():
            self.assertTrue(0 <= r.kast <= r.rounds)

    def test_placement_is_a_permutation(self):
        self.assertEqual(sorted(r.placement for r in self.s.records.values()), list(range(1, 11)))

    def test_strong_player_places_high(self):
        self.assertLessEqual(self.s.records["p0"].placement, 3)

    def test_empty_details(self):
        self.assertIsNone(parse_match_summary({}, ROLES))
        self.assertIsNone(parse_match_summary(None, ROLES))

    def test_trade_detection(self):
        det = {"matchInfo": {"matchId": "t", "queueID": "competitive", "mapId": "m", "gameStartMillis": 1},
               "players": [{"subject": s, "teamId": t, "characterId": "x", "stats": {"roundsPlayed": 1}} for s, t in
                           (("a", "Blue"), ("b", "Blue"), ("c", "Red"), ("d", "Red"))],
               "teams": [{"teamId": "Blue", "won": True, "roundsWon": 1}, {"teamId": "Red", "won": False, "roundsWon": 0}],
               "roundResults": [{"winningTeam": "Blue", "winningTeamRole": "Attacker", "playerStats": [
                   {"subject": "c", "damage": [], "kills": [{"killer": "c", "victim": "a", "timeSinceRoundStartMillis": 1000, "assistants": []}]},
                   {"subject": "b", "damage": [], "kills": [{"killer": "b", "victim": "c", "timeSinceRoundStartMillis": 3000, "assistants": []}]},
                   {"subject": "a", "damage": [], "kills": []}, {"subject": "d", "damage": [], "kills": []}]}]}
        s = parse_match_summary(det, {})
        self.assertEqual(s.records["b"].trade_k, 1)         # b отомстил за a в пределах 5 секунд
        self.assertEqual(s.records["a"].traded_d, 1)
        self.assertEqual((s.records["c"].fk, s.records["a"].fd), (1, 1))
        self.assertEqual(s.records["a"].kast, 1)            # смерть a «отомщена» → раунд засчитан в KAST

    def test_ffa_match_does_not_crash(self):
        det = make_details("ffa", P, ffa=True, rounds=1, queue="deathmatch", seed=5)
        s = parse_match_summary(det, ROLES)
        self.assertEqual(len(s.records), 10)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.details = {f"m{i}": make_details(f"m{i}", P, started=1_700_000_000_000 + i * 3_600_000, seed=i) for i in range(6)}
        self.riot = FakeRiot(self.details, {p: [f"m{i}" for i in range(5, -1, -1)] for p in P}, delay=0.01)
        self.pool = ThreadPoolExecutor(8)
        self.addCleanup(self.pool.shutdown)

    def test_concurrent_requests_download_once(self):
        store = MatchStore(ROLES)
        res = list(ThreadPoolExecutor(8).map(lambda _: store.get(self.riot, "m1"), range(8)))
        self.assertEqual(self.riot.calls["details"], 1)
        self.assertTrue(all(r is res[0] for r in res))

    def test_second_lookup_is_free(self):
        store = MatchStore(ROLES)
        store.get_many(self.riot, ["m0", "m1", "m2"], self.pool)
        store.get_many(self.riot, ["m1", "m2", "m3"], self.pool)
        self.assertEqual(self.riot.calls["details"], 4)

    def test_lru_limit(self):
        store = MatchStore(ROLES, max_items=3)
        store.get_many(self.riot, [f"m{i}" for i in range(6)], self.pool)
        self.assertEqual(len(store), 3)

    def test_missing_match_is_not_cached_as_success(self):
        store = MatchStore(ROLES)
        self.assertIsNone(store.get(self.riot, "nope"))
        self.assertIsNone(store.peek("nope"))

    def test_history_cache_and_extension(self):
        h = HistoryStore()
        a = h.entries(self.riot, "p0", "competitive", 3)
        b = h.entries(self.riot, "p0", "competitive", 3)
        self.assertEqual((len(a), len(b), self.riot.calls["history"]), (3, 3, 1))
        c = h.entries(self.riot, "p0", "competitive", 6)          # нужен более длинный список → ещё запрос
        self.assertEqual(len(c), 6)
        self.assertEqual(self.riot.calls["history"], 2)
        d = h.entries(self.riot, "p0", "competitive", 5)          # хватает закэшированного
        self.assertEqual((len(d), self.riot.calls["history"]), (5, 2))


class FetchPlayerTests(unittest.TestCase):
    def setUp(self):
        self.details = {f"m{i}": make_details(f"m{i}", P, started=1_700_000_000_000 + i * 3_600_000, seed=i) for i in range(8)}
        self.riot = FakeRiot(self.details, {p: [f"m{i}" for i in range(7, -1, -1)] for p in P})
        self.pool = ThreadPoolExecutor(8)
        self.addCleanup(self.pool.shutdown)

    def test_whole_lobby_downloads_each_match_once(self):
        store, hist = MatchStore(ROLES), HistoryStore()
        partials = []
        for p in P:
            info, recs = fetch_player(self.riot, p, seasons={"s1"}, season_dates={"s1": "2023-05-01T00:00:00Z"},
                                      agent_names=NAMES, mode=mode_for("competitive"), count=8, matches=store,
                                      history=hist, pool=self.pool, on_partial=partials.append)
            self.assertEqual(len(recs), 8)
        # раньше было бы 10 игроков × 8 матчей = 80 загрузок; теперь только уникальные матчи
        self.assertEqual(self.riot.calls["details"], 8)
        self.assertEqual(len(partials), 10)
        self.assertIsNone(partials[0]["kd"])                       # ранг приходит до статистики

    def test_records_sorted_newest_first(self):
        info, recs = fetch_player(self.riot, "p0", seasons={"s1"}, season_dates={}, agent_names=NAMES,
                                  mode=mode_for("competitive"), count=5, matches=MatchStore(ROLES), history=HistoryStore(),
                                  pool=self.pool)
        self.assertEqual([r.mid for r in recs], ["m7", "m6", "m5", "m4", "m3"])
        self.assertEqual(info["analyzed_count"], 5)

    def test_table_stats_formulas(self):
        s = parse_match_summary(self.details["m0"], ROLES)
        r = s.records["p0"]
        st = table_stats([r], NAMES)
        self.assertAlmostEqual(st["kd"], r.kills / max(r.deaths, 1))
        self.assertAlmostEqual(st["acs"], r.score / r.rounds)
        self.assertIn(st["streak"], ("+1W", "-1L"))

    def test_swiftplay_history_falls_back_to_competitive(self):
        info, recs = fetch_player(self.riot, "p0", seasons={"s1"}, season_dates={}, agent_names=NAMES,
                                  mode=mode_for("swiftplay"), count=5, matches=MatchStore(ROLES), history=HistoryStore(),
                                  pool=self.pool)
        self.assertEqual(len(recs), 5)           # в swiftplay матчей нет → добрали из соревновательных


class RankInfoTests(unittest.TestCase):
    MMR = {"LatestCompetitiveUpdate": {"TierAfterUpdate": 12, "RankedRatingAfterUpdate": 33, "SeasonID": "s2"},
           "QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {
               "S1": {"NumberOfGames": 30, "WinsByTier": {"15": 3, "9": 1}},
               "S2": {"NumberOfGames": 10, "WinsByTier": {"12": 4}},
               "OLD": {"NumberOfGames": 5, "WinsByTier": {"24": 1}}}}}}

    def test_peak_and_date(self):
        d = parse_rank_info(self.MMR, {"s1", "s2"}, {"s1": "2023-05-02T10:00:00Z", "s2": "2024-01-10T00:00:00Z"})
        self.assertEqual((d["cur_tier"], d["rr"], d["peak_tier"]), (12, 33, 15))
        self.assertEqual(d["peak_date"], "05.2023")                       # дата — акт, где достигнут пик
        self.assertEqual((d["total_games"], d["acts_played"]), (45, 3))

    def test_old_seasons_excluded_from_peak(self):
        d = parse_rank_info(self.MMR, {"s1"}, {"s1": "2023-05-02T10:00:00Z"})
        self.assertEqual(d["peak_tier"], 15)                              # 24 из старого акта не считается

    def test_current_rank_is_peak(self):
        mmr = {"LatestCompetitiveUpdate": {"TierAfterUpdate": 18, "RankedRatingAfterUpdate": 1, "SeasonID": "s2"},
               "QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {"S1": {"NumberOfGames": 3, "WinsByTier": {"15": 1}}}}}}
        d = parse_rank_info(mmr, {"s1", "s2"}, {"s1": "2023-05-02T00:00:00Z", "s2": "2024-01-10T00:00:00Z"})
        self.assertEqual((d["peak_tier"], d["peak_date"]), (18, "01.2024"))

    def test_none_mmr(self):
        d = parse_rank_info(None, set(), {})
        self.assertEqual((d["cur_tier"], d["peak_tier"], d["peak_date"]), (0, 0, None))


if __name__ == "__main__":
    unittest.main()
