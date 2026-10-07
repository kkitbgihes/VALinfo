"""Интеграционные тесты окна: вкладки, ленивые вычисления, пустой экран, режимы, темы и языки."""
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from valinfo import settings as S  # noqa: E402
from valinfo.i18n import translator  # noqa: E402
from valinfo.ui import theme  # noqa: E402
from valinfo.ui.main_window import MainWindow, TAB_KEYS  # noqa: E402
from valinfo.ui.theme import THEMES  # noqa: E402
from test_predictor_profile import build_payload  # noqa: E402
from test_ui_smoke import synthetic_match  # noqa: E402
from valinfo.analytics.post_match import parse_ultra_post_match  # noqa: E402


class _Assets:
    def agent_path(self, u): return Path("/nonexistent/a.png")
    def rank_path(self, t): return Path("/nonexistent/r.png")
    def flag_path(self, c): return Path("/nonexistent/f.png")
    def preload(self, *a, **k): pass


def wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def post_data():
    parsed = parse_ultra_post_match(synthetic_match(), {}, {}, "p0")
    parsed["map_name"] = "Ascent"
    parsed["countries"] = {}
    return parsed


class WindowTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = S.Settings(Path(self.tmp.name) / "s.json")
        S.set_settings(self.settings)
        translator.set_language("en")
        theme.apply_theme("valorant")
        self.win = MainWindow(_Assets(), start_worker=False, settings=self.settings)
        self.addCleanup(self.win.close)
        self.win.show()

    def push_live(self, payload):
        self.win._pending_live = payload
        self.win._flush_live()


class TabTests(WindowTestBase):
    def test_four_tabs_in_order(self):
        self.assertEqual(TAB_KEYS, ("live", "prediction", "players", "settings"))
        self.assertEqual([self.win.nav._buttons[k].text() for k in TAB_KEYS], ["MATCH", "FORECAST", "PLAYERS", "SETTINGS"])

    def test_hidden_tabs_do_no_work(self):
        with mock.patch.object(self.win.service, "request", return_value=None) as req:
            self.push_live(build_payload())
            self.assertEqual(req.call_count, 0)                  # вкладки анализа скрыты → ничего не считается
            self.win.switch_tab("prediction")
            self.assertEqual(req.call_count, 1)
            self.win.switch_tab("live")
            self.push_live(build_payload())                      # данные обновились, но вкладка скрыта
            self.assertEqual(req.call_count, 1)

    def test_analysis_runs_in_background_and_renders(self):
        self.push_live(build_payload(strong=("p1",)))
        self.win.switch_tab("prediction")
        pv = self.win.tabs["prediction"]
        self.assertTrue(wait(lambda: pv._result is not None))
        self.assertGreaterEqual(len(pv.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QTableWidget"]).QTableWidget)), 2)
        self.win.switch_tab("players")
        pl = self.win.tabs["players"]
        self.assertTrue(wait(lambda: pl._result is not None))
        self.assertEqual(pl._table.rowCount(), 10)

    def test_returning_to_a_tab_uses_cache(self):
        self.push_live(build_payload())
        self.win.switch_tab("players")
        pl = self.win.tabs["players"]
        self.assertTrue(wait(lambda: pl._result is not None))
        self.win.switch_tab("live")
        with mock.patch("valinfo.ui.analysis_service.analyze_players") as heavy:
            self.win.switch_tab("players")
            self.assertEqual(heavy.call_count, 0)                # то же самое состояние → пересчёта нет

    def test_unsupported_mode_message(self):
        self.push_live(build_payload(queue="spikerush"))
        self.win.switch_tab("prediction")
        self.assertIn("No forecast", self.win.tabs["prediction"]._empty.lbl_title.text())

    def test_pregame_forecast_waits_for_enemies(self):
        pl = build_payload()
        pl["players"] = pl["players"][:5]
        self.push_live(pl)
        self.win.switch_tab("prediction")
        self.assertIn("both teams", self.win.tabs["prediction"]._empty.lbl_title.text())

    def test_incomplete_data_shows_progress_not_a_forecast(self):
        self.push_live(build_payload(info_done=False))
        self.win.switch_tab("prediction")
        self.assertIn("3 of 10", self.win.tabs["prediction"]._empty.lbl_sub.text())

    def test_new_match_does_not_show_stale_forecast(self):
        self.push_live(build_payload())
        self.win.switch_tab("prediction")
        pv = self.win.tabs["prediction"]
        self.assertTrue(wait(lambda: pv._result is not None))
        other = build_payload(info_done=False)
        other["mid"] = "another"
        self.push_live(other)
        self.assertIsNone(pv._result)


class IdleStateTests(WindowTestBase):
    def test_starts_empty(self):
        lt = self.win.live_tab
        self.assertIs(lt.stack.currentWidget(), lt.idle_view)
        self.assertIn("Waiting for a match", lt.idle_view.lbl_title.text())

    def test_live_then_no_match_clears_everything(self):
        from test_ui_smoke import live_payload
        self.push_live(live_payload())
        lt = self.win.live_tab
        self.assertIs(lt.stack.currentWidget(), lt.live_view)
        self.assertEqual(lt.live_view.table_ally.rowCount(), 5)
        self.win._on_no_match()
        self.assertIs(lt.stack.currentWidget(), lt.idle_view)
        self.assertEqual(lt.live_view.table_ally.rowCount(), 0)       # никаких «обрывков» прошлого матча
        self.assertEqual(lt.live_view.table_enemy.rowCount(), 0)
        self.assertIsNone(self.win._payload)

    def test_post_match_back_goes_to_idle_not_stale_table(self):
        from test_ui_smoke import live_payload
        self.push_live(live_payload())
        self.win._on_post_match(post_data())
        lt = self.win.live_tab
        self.assertIs(lt.stack.currentWidget(), lt.post_view)
        self.win._on_no_match()                                       # воркер каждый тик шлёт «матча нет»
        self.assertIs(lt.stack.currentWidget(), lt.post_view)         # итоги остаются, пока пользователь не нажмёт «назад»
        lt.post_view.back_requested.emit()
        self.assertIs(lt.stack.currentWidget(), lt.idle_view)
        self.assertEqual(lt.live_view.table_ally.rowCount(), 0)

    def test_new_match_leaves_post_screen(self):
        from test_ui_smoke import live_payload
        self.win._on_post_match(post_data())
        self.push_live(live_payload())
        self.assertIs(self.win.live_tab.stack.currentWidget(), self.win.live_view)

    def test_analysis_tabs_empty_after_match_ends(self):
        self.push_live(build_payload())
        self.win.switch_tab("players")
        pl = self.win.tabs["players"]
        self.assertTrue(wait(lambda: pl._result is not None))
        self.win._on_no_match()
        self.assertIsNone(pl._result)
        self.assertIn("No active match", pl._empty.lbl_title.text())

    def test_live_view_not_rendered_while_on_other_tab(self):
        from test_ui_smoke import live_payload
        self.win.switch_tab("settings")
        self.push_live(live_payload())
        self.assertEqual(self.win.live_view.table_ally.rowCount(), 0)  # вкладка скрыта → таблицы не строятся
        self.win.switch_tab("live")
        self.assertEqual(self.win.live_view.table_ally.rowCount(), 5)  # вернулись → свежее состояние


class ModeLayoutTests(WindowTestBase):
    def test_deathmatch_is_one_table_of_ten(self):
        pl = build_payload(queue="deathmatch", ffa=True)
        pl["layout"] = "ffa"
        self.push_live(pl)
        lv = self.win.live_view
        self.assertEqual(lv.table_ally.rowCount(), 10)
        self.assertTrue(lv.card_enemy.isHidden())
        self.assertIn("PLAYERS", lv.card_ally.title_lbl.text())

    def test_switching_back_to_teams_restores_two_tables(self):
        ffa = build_payload(queue="deathmatch", ffa=True)
        self.push_live(ffa)
        self.push_live(build_payload())
        lv = self.win.live_view
        self.assertEqual((lv.table_ally.rowCount(), lv.table_enemy.rowCount()), (5, 5))
        self.assertFalse(lv.card_enemy.isHidden())
        self.assertIn("YOUR TEAM", lv.card_ally.title_lbl.text())

    def test_unknown_mode_falls_back_to_teams(self):
        pl = build_payload(queue="some-new-mode")
        self.push_live(pl)
        self.assertEqual(self.win.live_view.table_enemy.rowCount(), 5)

    def test_swiftplay_and_unrated_render(self):
        for q in ("swiftplay", "unrated", "premier-seasonmatch"):
            self.push_live(build_payload(queue=q))
            self.assertEqual(self.win.live_view.table_ally.rowCount(), 5)


class PeakDateTests(WindowTestBase):
    def _payload(self):
        pl = build_payload()
        for p in pl["info"]:
            pl["info"][p]["peak_date"] = "05.2023"
        return pl

    def _texts(self):
        from PyQt6.QtWidgets import QLabel
        t = self.win.live_view.table_ally
        return [l.text() for l in t.cellWidget(0, 4).findChildren(QLabel)]

    def test_peak_date_shown_next_to_peak(self):
        self.push_live(self._payload())
        self.assertIn("05.2023", self._texts())

    def test_peak_date_can_be_hidden(self):
        self.push_live(self._payload())
        self.settings.set("show_peak_date", False)
        self.assertNotIn("05.2023", self._texts())
        self.settings.set("show_peak_date", True)
        self.assertIn("05.2023", self._texts())

    def test_no_date_for_unranked_peak(self):
        pl = self._payload()
        pl["info"]["p0"].update(peak_tier=0, peak_date="05.2023")
        self.push_live(pl)
        self.assertNotIn("05.2023", self._texts())


class SettingsEffectTests(WindowTestBase):
    def test_every_theme_and_language_renders_every_tab(self):
        self.push_live(build_payload(strong=("p2",), levels={"p6": 20}))
        for lang in ("ru", "en"):
            self.settings.set("language", lang)
            for key in THEMES:
                self.settings.set("theme", key)
                for tab in TAB_KEYS:
                    self.win.switch_tab(tab)
                    if tab in ("prediction", "players"):
                        t = self.win.tabs[tab]
                        self.assertTrue(wait(lambda: t._result is not None), (lang, key, tab))
                    app.processEvents()
        self.assertEqual(theme.c.key, "daylight")
        self.assertIn(THEMES["daylight"].bg, self.win.styleSheet())

    def test_language_switch_translates_live_ui(self):
        from test_ui_smoke import live_payload
        self.push_live(live_payload())
        self.settings.set("language", "ru")
        self.assertEqual(self.win.nav._buttons["live"].text(), "МАТЧ")
        self.assertIn("ТВОЯ КОМАНДА", self.win.live_view.card_ally.title_lbl.text())
        self.assertEqual(self.win.live_view.table_ally.horizontalHeaderItem(0).text(), "Агент")
        self.settings.set("language", "en")
        self.assertEqual(self.win.nav._buttons["live"].text(), "MATCH")

    def test_country_toggle_hides_column_and_legend(self):
        self.assertFalse(self.win.live_view.table_ally.isColumnHidden(1))
        self.settings.set("country_detection", False)
        self.assertTrue(self.win.live_view.table_ally.isColumnHidden(1))
        self.assertEqual(self.win._legend_host.count(), 0)
        self.settings.set("country_detection", True)
        self.assertFalse(self.win.live_view.table_ally.isColumnHidden(1))
        self.assertEqual(self.win._legend_host.count(), 1)

    def test_country_off_creates_no_fetcher(self):
        self.settings.set("country_detection", False)
        self.assertIsNone(self.win.worker._country_fetcher())
        self.assertIsNone(self.win.worker._country)

    def test_party_toggle_removes_color_bars(self):
        from test_ui_smoke import live_payload
        self.push_live(live_payload())
        self.settings.set("party_detection", False)
        self.assertEqual(self.win.live_view.card_ally.status_lbl.isVisible(), False)

    def test_always_on_top_flag(self):
        from PyQt6.QtCore import Qt
        self.settings.set("always_on_top", True)
        self.assertTrue(self.win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.win.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.settings.set("always_on_top", False)
        self.assertFalse(self.win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_settings_survive_restart(self):
        self.settings.set("theme", "emerald")
        self.settings.set("language", "ru")
        again = S.Settings(self.settings.path).load()
        self.assertEqual((again.theme, again.language), ("emerald", "ru"))

    def test_status_is_translated(self):
        self.win._on_status("status.match", {"map": "ASCENT", "state": "ingame", "mode": "swiftplay", "loading": True})
        self.assertIn("Swiftplay", self.win.lbl_status.text())
        self.assertIn("loading data", self.win.lbl_status.text())
        self.settings.set("language", "ru")
        self.assertIn("Быстрая игра", self.win.lbl_status.text())


class NoMatchSavingTests(unittest.TestCase):
    def test_json_saving_is_gone(self):
        root = Path(__file__).resolve().parents[1] / "src" / "valinfo"
        self.assertFalse((root / "storage" / "matches.py").exists())
        offenders = []
        for f in root.rglob("*.py"):
            src = f.read_text(encoding="utf-8")
            if any(w in src for w in ("save_match_to_disk", "MATCH_SAVE_DIR", "SAVE_JSON_INDENT")):
                offenders.append(f.name)
        self.assertEqual(offenders, [])

    def test_startup_creates_no_matches_folder(self):
        from valinfo import paths
        self.assertFalse(hasattr(paths, "MATCH_SAVE_DIR"))


if __name__ == "__main__":
    unittest.main()
