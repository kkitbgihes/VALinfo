import math
import time
import unittest

from valinfo.analytics.lobby import build_lobby, payload_signature
from valinfo.analytics.predictor import RULESETS, analyze, score_distribution, win_prob_of, win_at
from valinfo.analytics.profile import TRAITS, analyze_players, smurf_estimate, compute_metrics
from valinfo.analytics.records import parse_match_summary
from valinfo.analytics.skill import Cfg
from valinfo.modes import BOMB_13, SWIFT_5
from helpers import AGENT_LIST, NAMES, ROLES, make_details

P = [f"p{i}" for i in range(10)]


def build_payload(strong=(), weak_enemy=(), queue="competitive", n_matches=10, levels=None, tiers=None, ffa=False,
                  info_done=True):
    """Payload как у воркера, но с историей, сгенерированной синтетически."""
    recs = {p: [] for p in P}
    for m in range(n_matches):
        det = make_details(f"m{m}", P, started=int(time.time() * 1000) - (m + 1) * 3_600_000, strong=strong, seed=m,
                           queue=queue, ffa=ffa, tiers=tiers)
        s = parse_match_summary(det, ROLES)
        for p in P:
            recs[p].append(s.records[p])
    players = []
    for i, p in enumerate(P):
        players.append({"subject": p, "team": p if ffa else ("Blue" if i < 5 else "Red"), "agent": AGENT_LIST[i % 8],
                        "ident": {"AccountLevel": (levels or {}).get(p, 120), "Incognito": False}})
    info = {p: {"cur_tier": (tiers or {}).get(p, 12), "rr": 40, "peak_tier": 15, "total_games": 300, "acts_played": 5}
            for p in P}
    return {"kind": "ingame", "mid": "live", "map_name": "Ascent", "map_url": "/Game/Maps/Ascent", "queue": queue,
            "layout": "ffa" if ffa else "teams", "players": players, "info": info,
            "info_done": set(P) if info_done else set(P[:3]), "names": {p: (f"N{i}", "T") for i, p in enumerate(P)},
            "agents": NAMES, "roles": ROLES, "me": "p0", "records": recs, "parties": {}, "party_status": {}, "countries": {}}


class ScoreMathTests(unittest.TestCase):
    def test_distributions_are_normalized(self):
        for rs in RULESETS.values():
            for pa, pd in ((0.5, 0.5), (0.6, 0.45), (0.3, 0.4), (0.9, 0.9)):
                for start in (True, False):
                    d = score_distribution(pa, pd, start, rs)
                    self.assertAlmostEqual(sum(d.values()), 1.0, places=6)

    def test_symmetric_teams_are_fifty_fifty(self):
        for rs in RULESETS.values():
            self.assertAlmostEqual(win_prob_of(score_distribution(0.5, 0.5, True, rs)), 0.5, places=9)

    def test_monotonic_in_round_probability(self):
        rs = RULESETS[BOMB_13]
        ws = [win_prob_of(score_distribution(p, p, True, rs)) for p in (0.40, 0.45, 0.50, 0.55, 0.60)]
        self.assertEqual(ws, sorted(ws))

    def test_known_value_13_round_game(self):
        # p=0.55 в каждом раунде → матч до 13 (овертайм до +2) выигрывается с вероятностью ≈ 0.7007.
        # Значение сверено двумя независимыми способами: точный перебор состояний (a, b) и Monte-Carlo на 400k матчей.
        w = win_prob_of(score_distribution(0.55, 0.55, True, RULESETS[BOMB_13]))
        self.assertAlmostEqual(w, 0.7007, places=3)

    def test_swiftplay_is_shorter_and_noisier(self):
        a = win_prob_of(score_distribution(0.55, 0.55, True, RULESETS[BOMB_13]))
        b = win_prob_of(score_distribution(0.55, 0.55, True, RULESETS[SWIFT_5]))
        self.assertLess(b, a)                       # короткий матч → больше случайности
        d = score_distribution(0.5, 0.5, True, RULESETS[SWIFT_5])
        self.assertTrue(all(max(k) <= 5 for k in d))

    def test_overtime_scores_exist_only_in_13(self):
        d = score_distribution(0.5, 0.5, True, RULESETS[BOMB_13])
        self.assertTrue(any(max(k) > 13 for k in d))
        for (a, b) in d:
            if max(a, b) > 13:
                self.assertEqual(abs(a - b), 2)


class PredictorTests(unittest.TestCase):
    def test_forecast_runs_and_is_sane(self):
        fc = analyze(build_lobby(build_payload()))
        self.assertTrue(0 < fc.win_prob < 1)
        self.assertLessEqual(fc.win_lo, fc.win_prob + 1e-9)
        self.assertGreaterEqual(fc.win_hi, fc.win_prob - 1e-9)
        self.assertAlmostEqual(sum(fc.dist.values()), 1.0, places=6)
        self.assertEqual((len(fc.ally), len(fc.enemy)), (5, 5))
        self.assertAlmostEqual(sum(pf.p_mvp_lobby for pf in fc.ally + fc.enemy), 1.0, places=6)
        self.assertAlmostEqual(sum(pf.p_best_team for pf in fc.ally), 1.0, places=6)

    def test_deterministic(self):
        pl = build_payload(strong=("p1",))
        a, b = analyze(build_lobby(pl)), analyze(build_lobby(pl))
        self.assertEqual(a.win_prob, b.win_prob)
        self.assertEqual([p.exp_acs for p in a.ally], [p.exp_acs for p in b.ally])

    def test_higher_rank_helps(self):
        base = analyze(build_lobby(build_payload())).win_prob
        up = analyze(build_lobby(build_payload(tiers={p: 18 for p in P[:5]}))).win_prob
        self.assertGreater(up, base + 0.1)

    def test_strong_ally_form_helps(self):
        # на уровне ранга игроки равны; форма сильного союзника должна сместить шансы
        base = analyze(build_lobby(build_payload())).win_prob
        strong = analyze(build_lobby(build_payload(strong=("p1", "p2")))).win_prob
        self.assertGreater(strong, base)

    def test_swapping_teams_mirrors_probability(self):
        pl = build_payload(tiers={p: 15 for p in P[:5]})
        a = analyze(build_lobby(pl)).win_prob
        flipped = dict(pl)
        flipped["me"] = "p5"                                  # смотрим из команды противников
        b = analyze(build_lobby(flipped)).win_prob
        self.assertAlmostEqual(a + b, 1.0, delta=0.02)

    def test_reasons_present_and_signed(self):
        fc = analyze(build_lobby(build_payload(tiers={p: 18 for p in P[:5]})))
        kinds = [r.kind for r in fc.reasons]
        self.assertIn("rank", kinds)
        self.assertIn("confidence", kinds)
        rank = next(r for r in fc.reasons if r.kind == "rank")
        self.assertGreater(rank.impact, 0.05)
        impacts = [abs(r.impact) for r in fc.reasons if r.impact is not None]
        self.assertEqual(impacts, sorted(impacts, reverse=True))      # самые весомые причины — первыми

    def test_works_without_any_history(self):
        pl = build_payload()
        pl["records"] = {p: [] for p in P}
        fc = analyze(build_lobby(pl))
        self.assertEqual(fc.confidence, "low")
        self.assertTrue(0.3 < fc.win_prob < 0.7)

    def test_swiftplay_and_unranked_modes(self):
        for q in ("swiftplay", "unrated"):
            fc = analyze(build_lobby(build_payload(queue=q)))
            self.assertTrue(0 < fc.win_prob < 1)
        self.assertEqual(analyze(build_lobby(build_payload(queue="swiftplay"))).ruleset.win, 5)

    def test_unsupported_mode_raises(self):
        with self.assertRaises(ValueError):
            analyze(build_lobby(build_payload(queue="spikerush")))

    def test_performance_budget(self):
        lobby = build_lobby(build_payload(n_matches=20))
        t = time.perf_counter()
        analyze(lobby)
        self.assertLess(time.perf_counter() - t, 1.5)                  # тяжёлый случай: 10 игроков × 20 матчей


class ProfileTests(unittest.TestCase):
    def test_analysis_covers_everyone(self):
        an = analyze_players(build_lobby(build_payload(strong=("p3",))))
        self.assertEqual(set(an.profiles), set(P))
        self.assertEqual(an.profiles[an.strongest].puuid, an.order[0])
        for pr in an.profiles.values():
            self.assertTrue(0 <= pr.threat <= 100)
            self.assertTrue(0 <= pr.smurf.p <= 1)
            self.assertEqual(len(pr.radar), 6)
            self.assertTrue(all(0 <= v <= 1 for _, v in pr.radar))
        self.assertLessEqual(an.profiles["p3"].lobby_rank, 3)           # сильный по статам игрок — в топе

    def test_rank_drives_threat(self):
        an = analyze_players(build_lobby(build_payload(tiers={"p6": 24})))
        self.assertEqual(an.profiles["p6"].lobby_rank, 1)

    def test_traits_are_varied_and_valid(self):
        an = analyze_players(build_lobby(build_payload(strong=("p2", "p7"))))
        best = [pr.best.key for pr in an.profiles.values() if pr.best]
        worst = [pr.worst.key for pr in an.profiles.values() if pr.worst]
        self.assertGreaterEqual(len(best), 6)
        self.assertGreaterEqual(len(set(best)), 5)                        # стороны не повторяются у всех подряд
        self.assertGreaterEqual(len(set(worst)), 5)
        valid = {t.key for t in TRAITS}
        for pr in an.profiles.values():
            for hit in (pr.best, pr.worst):
                if hit:
                    self.assertIn(hit.key, valid)
                    self.assertIn(hit.variant, ("best", "good", "bad", "worst"))
                    self.assertGreaterEqual(hit.rank, 1)
                    self.assertLessEqual(hit.rank, hit.of)

    def test_best_and_worst_differ(self):
        an = analyze_players(build_lobby(build_payload()))
        for pr in an.profiles.values():
            if pr.best and pr.worst:
                self.assertNotEqual((pr.best.key, pr.best.side), (pr.worst.key, pr.worst.side))

    def test_smurf_detection(self):
        pl = build_payload(strong=("p4",), levels={"p4": 18, "p5": 200})
        pl["info"]["p4"].update(total_games=25, acts_played=1)
        an = analyze_players(build_lobby(pl))
        self.assertGreater(an.profiles["p4"].smurf.p, 0.5)
        self.assertLess(an.profiles["p5"].smurf.p, 0.2)
        self.assertGreater(an.profiles["p4"].smurf.p, an.profiles["p5"].smurf.p)
        self.assertTrue(an.profiles["p4"].smurf.signals)

    def test_smurf_without_data_is_low(self):
        pl = build_payload()
        pl["records"]["p2"] = []
        pl["players"][2]["ident"]["HideAccountLevel"] = True
        self.assertLess(analyze_players(build_lobby(pl)).profiles["p2"].smurf.p, 0.15)

    def test_ffa_lobby(self):
        an = analyze_players(build_lobby(build_payload(queue="deathmatch", ffa=True)))
        self.assertEqual(len(an.profiles), 10)
        self.assertTrue(all(pr.team == "ffa" for pr in an.profiles.values()))

    def test_handles_empty_histories(self):
        pl = build_payload()
        pl["records"] = {p: [] for p in P}
        an = analyze_players(build_lobby(pl))
        self.assertTrue(all(pr.n == 0 and pr.style == "style.unknown" for pr in an.profiles.values()))

    def test_session_detection(self):
        lobby = build_lobby(build_payload())
        m = compute_metrics(lobby.players[0], Cfg())
        self.assertGreaterEqual(m["session_n"], 2)                         # матчи были «час назад» подряд

    def test_performance_budget(self):
        lobby = build_lobby(build_payload(n_matches=20))
        t = time.perf_counter()
        analyze_players(lobby)
        self.assertLess(time.perf_counter() - t, 1.0)


class SignatureTests(unittest.TestCase):
    def test_signature_changes_only_with_inputs(self):
        a = build_payload()
        self.assertEqual(payload_signature(a, "x"), payload_signature(dict(a), "x"))
        b = build_payload()
        b["info"]["p1"]["cur_tier"] = 20
        self.assertNotEqual(payload_signature(a, "x"), payload_signature(b, "x"))
        self.assertNotEqual(payload_signature(a, "x"), payload_signature(a, "y"))
        c = build_payload(info_done=False)
        self.assertNotEqual(payload_signature(a, "x"), payload_signature(c, "x"))


if __name__ == "__main__":
    unittest.main()
