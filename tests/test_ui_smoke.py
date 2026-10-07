"""Смоук-тесты UI на настоящих виджетах (QT_QPA_PLATFORM=offscreen)."""
import os
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REAL_QT = True

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from valinfo.analytics.post_match import parse_ultra_post_match  # noqa: E402
from valinfo.models import CountryResult, CountryStatus as S  # noqa: E402

_app = QApplication.instance() or QApplication([])

from valinfo.ui import live_view as LV  # noqa: E402
from valinfo.ui import post_view as PV  # noqa: E402
from valinfo.ui import widgets as W  # noqa: E402
from valinfo.ui.icons import IconProvider  # noqa: E402


class _Assets:
    def agent_path(self, u): return Path("/nonexistent/a.png")
    def rank_path(self, t): return Path("/nonexistent/r.png")
    def flag_path(self, c): return Path("/nonexistent/f.png")


def live_payload(countries=None, names_missing=()):
    ids = [f"p{i}" for i in range(10)]
    players = []
    for i, s in enumerate(ids):
        players.append({"subject": s, "team": "Blue" if i < 5 else "Red", "agent": f"AG{i}".lower(),
                        "ident": {"Incognito": i == 7, "AccountLevel": 50 + i, "HideAccountLevel": i == 3}})
    names = {s: (f"Nick{i}", f"T{i}") for i, s in enumerate(ids) if s not in names_missing}
    info = {s: {"cur_tier": 12, "rr": 40, "peak_tier": 15, "kd": 1.2, "hs": 20.0, "adr": 140.0, "acs": 230.0,
                "winrate": 52.0, "streak": "+2W", "main_agent_uuid": "ag1", "main_agent": "Jett"} for s in ids[:6]}
    return {"kind": "ingame", "mid": "m", "map_name": "Ascent", "queue": "competitive", "layout": "teams",
            "players": players, "info": info,
            "info_done": set(ids[:6]), "names": names, "agents": {f"ag{i}": f"Agent{i}" for i in range(10)},
            "me": "p0", "parties": {"p1": 0, "p2": 0}, "party_status": {"Blue": "done", "Red": "pending"},
            "countries": countries or {}}


def synthetic_match():
    pl = []
    for i in range(4):
        pl.append({"subject": f"p{i}", "teamId": "Blue" if i < 2 else "Red", "characterId": f"AG{i}",
                   "gameName": f"Nick{i}", "tagLine": f"T{i}",
                   "stats": {"kills": 10 + i, "deaths": 8, "assists": 3, "score": 3000 + i, "roundsPlayed": 2,
                             "abilityCasts": {"grenadeCasts": 1, "ability1Casts": 2, "ability2Casts": 3, "ultimateCasts": 1}}})
    rounds = [{"winningTeam": "Blue", "roundResultCode": "Elimination", "plantSite": "",
               "playerStats": [{"subject": "p0", "kills": [{"victim": "p2", "timeSinceGameStartMillis": 100,
                                                            "finishingDamage": {"damageItem": "Vandal"}}],
                                "damage": [{"damage": 150, "headshots": 1, "bodyshots": 1, "legshots": 0}],
                                "economy": {"spent": 3900}},
                               {"subject": "p2", "kills": [], "damage": [], "economy": {"spent": 2000}}]},
              {"winningTeam": "Red", "roundResultCode": "Detonate", "plantSite": "A", "bombPlanter": "p3",
               "playerStats": []}]
    return {"matchInfo": {"mapId": "/Game/Maps/Ascent", "queueID": "competitive", "gameLengthMillis": 1},
            "players": pl, "roundResults": rounds,
            "teams": [{"teamId": "Blue", "won": True, "roundsWon": 13, "numPoints": 13},
                      {"teamId": "Red", "won": False, "roundsWon": 5, "numPoints": 5}]}


class _Event:
    def button(self): return Qt.MouseButton.LeftButton
    def position(self):
        class P:
            def toPoint(self_inner): return QPoint(1, 1)
        return P()
    def accept(self): pass


class NickCopyTests(unittest.TestCase):
    def test_click_copies_nickname_hash_tag(self):
        n = W.NickLabel("Display ◄", "Real#EUW1", "#fff", True)
        n.mouseReleaseEvent(_Event())
        self.assertEqual(QGuiApplication.clipboard().text(), "Real#EUW1")

    def test_animation_state_survives_row_rebuild(self):
        W._last_copy["key"], W._last_copy["t"] = None, 0
        a = W.NickLabel("A", "Same#1", "#fff")
        a.mouseReleaseEvent(_Event())
        with mock.patch.object(W.NickLabel, "_play") as play:
            W.NickLabel("A", "Same#1", "#fff")        # «перерисованная» строка
            self.assertTrue(play.called)
            self.assertLess(play.call_args[0][0], W.COPY_ANIM_MS)
            play.reset_mock()
            W.NickLabel("B", "Other#2", "#fff")
            self.assertFalse(play.called)

    def test_flash_toggles_with_progress(self):
        n = W.NickLabel("A", "A#1", "#fff")
        n._on_anim(0.3)
        self.assertTrue(n._flash)
        n._on_anim(0.9)
        self.assertFalse(n._flash)
        n._on_done()
        self.assertFalse(n._flash)

    def test_cell_without_copy_text_is_plain(self):
        made = []

        def spy(*a, **k):                         # настоящий виджет + запись вызова (MagicMock Qt не принимает)
            made.append(a)
            return W.QLabel("x")

        with mock.patch.object(W, "NickLabel", spy):
            W.create_nick_cell("Jett (Hidden)", None)
            self.assertEqual(made, [])
            W.create_nick_cell("Name#T", "Name#T")
            self.assertEqual(len(made), 1)


class LiveViewTests(unittest.TestCase):
    def setUp(self):
        self.view = LV.LiveView(IconProvider(_Assets()))
        self.nick_calls, self.country_calls = [], []
        real_nick, real_country = LV.create_nick_cell, LV.build_country_cell

        def nick_spy(*a, **k):
            self.nick_calls.append(a)
            return real_nick(*a, **k)

        def country_spy(result, icons):
            self.country_calls.append(result)
            return real_country(result, icons)

        for target, spy in (("create_nick_cell", nick_spy), ("build_country_cell", country_spy)):
            p = mock.patch.object(LV, target, spy)
            p.start()
            self.addCleanup(p.stop)

    def test_nicks_are_copyable_except_hidden_and_loading(self):
        data = live_payload(names_missing=("p4",))
        self.view.update_data(data)
        by_display = {c[0]: c[1] for c in self.nick_calls}
        self.assertEqual(by_display["Nick1#T1"], "Nick1#T1")          # копируется ровно nickname#tag
        self.assertEqual(by_display["Nick0#T0 ◄"], "Nick0#T0")         # маркер «это вы» в буфер не попадает
        self.assertIsNone(by_display["Agent7 (Hidden)"])               # скрытый ник — не копируется
        self.assertIsNone(by_display["Loading..."])

    def test_country_cells_by_status(self):
        c = {"p1": CountryResult(S.FOUND, "DE"), "p2": CountryResult(S.PRIVATE),
             "p3": CountryResult(S.NO_COUNTRY), "p4": CountryResult(S.BLOCKED, detail="cf"),
             "p5": CountryResult(S.LOADING)}
        self.view.update_data(live_payload(countries=c))
        got = Counter(r.status if r else None for r in self.country_calls)
        self.assertEqual(got[S.FOUND], 1)
        self.assertEqual(got[S.PRIVATE], 1)
        self.assertEqual(got[S.NO_COUNTRY], 1)
        self.assertEqual(got[S.BLOCKED], 1)
        self.assertEqual(got[None], 2)                                 # я (p0) и скрытый (p7)
        self.assertEqual(got[S.LOADING], 4)                            # p5 явно + p6,p8,p9 без записи (= ещё ищем)

    def test_pregame_has_no_country_cells(self):
        p = mock.patch.object(LV, "COUNTRY_ON_PREGAME", False)      # в config.py по умолчанию True — фиксируем явно
        p.start()
        self.addCleanup(p.stop)
        data = live_payload()
        data["kind"] = "pregame"
        self.view.update_data(data)
        self.assertTrue(all(r is None for r in self.country_calls))

    def test_identical_update_rebuilds_nothing(self):
        data = live_payload()
        self.view.update_data(data)
        n = len(self.nick_calls)
        self.view.update_data(live_payload())
        self.assertEqual(len(self.nick_calls), n)

    def test_only_changed_row_is_rebuilt(self):
        self.view.update_data(live_payload())
        self.nick_calls.clear()
        self.view.update_data(live_payload(countries={"p2": CountryResult(S.FOUND, "FR")}))
        self.assertEqual(len(self.nick_calls), 1)
        self.assertEqual(self.nick_calls[0][1], "Nick2#T2")

    def test_party_reorder_rebuilds_affected_rows(self):
        self.view.update_data(live_payload())
        self.nick_calls.clear()
        d = live_payload()
        d["parties"] = {"p3": 0, "p4": 0}      # пати переехала → порядок и цветные полоски меняются
        self.view.update_data(d)
        self.assertGreaterEqual(len(self.nick_calls), 2)


class PostViewTests(unittest.TestCase):
    def test_country_column_and_copy_in_post_match(self):
        parsed = parse_ultra_post_match(synthetic_match(), {}, {f"ag{i}": f"Agent{i}" for i in range(4)}, "p0")
        self.assertIsNotNone(parsed)
        parsed["map_name"] = "Ascent"
        parsed["countries"] = {
            "p0": CountryResult(S.SKIPPED, detail="This is you").to_dict(),
            "p1": CountryResult(S.FOUND, "PL").to_dict(),
            "p2": CountryResult(S.PRIVATE).to_dict(),
            "p3": CountryResult(S.INTERRUPTED).to_dict(),
        }
        view = PV.PostMatchView(IconProvider(_Assets()))
        nick_calls, country_calls = [], []
        real_nick, real_country = PV.create_nick_cell, PV.build_country_cell
        with mock.patch.object(PV, "create_nick_cell", lambda *a, **k: (nick_calls.append(a), real_nick(*a, **k))[1]), \
             mock.patch.object(PV, "build_country_cell", lambda r, i: (country_calls.append(r), real_country(r, i))[1]):
            view.show_data(parsed)

        self.assertEqual(PV.SUMMARY_KEYS[PV.COL_COUNTRY], "country")
        self.assertEqual(view.table_summary.horizontalHeaderItem(PV.COL_COUNTRY).text(), "Country")
        status_by_row = {r.status for r in country_calls}
        self.assertEqual(status_by_row, {S.SKIPPED, S.FOUND, S.PRIVATE, S.INTERRUPTED})
        copies = {c[1] for c in nick_calls}
        self.assertTrue({"Nick1#T1", "Nick2#T2"} <= copies)            # summary + eco
        self.assertEqual(view.table_rounds.rowCount(), 2)

    def test_missing_countries_key_does_not_crash(self):
        parsed = parse_ultra_post_match(synthetic_match(), {}, {}, "p0")
        parsed["map_name"] = "Ascent"
        PV.PostMatchView(IconProvider(_Assets())).show_data(parsed)    # старые данные без "countries"


if __name__ == "__main__":
    unittest.main()
