import json
import re
import tempfile
import unittest
from pathlib import Path

from valinfo import settings as S
from valinfo.i18n import Translator
from valinfo.modes import FFA, MODES, TEAMS, detect_layout, mode_for

ROOT = Path(__file__).resolve().parents[1] / "src" / "valinfo"
LOCALES = ROOT / "i18n" / "locales"
PH = re.compile(r"\{(\w+)\}")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "s.json"

    def test_defaults_and_roundtrip(self):
        s = S.Settings(self.path).load()
        self.assertEqual((s.language, s.theme, s.analysis_matches), ("auto", "valorant", 10))
        s.set("theme", "violet"); s.set("analysis_matches", 15); s.set("country_detection", False)
        s2 = S.Settings(self.path).load()
        self.assertEqual((s2.theme, s2.analysis_matches, s2.country_detection), ("violet", 15, False))

    def test_garbage_is_repaired(self):
        self.path.write_text('{"analysis_matches": "lots", "theme": 5, "country_detection": "yes", "unknown": 1}')
        s = S.Settings(self.path).load()
        self.assertEqual(s.analysis_matches, 10)
        self.assertEqual(s.theme, "valorant")
        self.assertIs(s.country_detection, True)             # строка вместо bool → остаётся значение по умолчанию
        self.path.write_text("not json at all")
        self.assertEqual(S.Settings(self.path).load().theme, "valorant")

    def test_analysis_value_snaps_to_allowed(self):
        s = S.Settings(self.path)
        s.set("analysis_matches", 12)
        self.assertIn(s.analysis_matches, S.ANALYSIS_CHOICES)

    def test_listener_called_only_on_change(self):
        s = S.Settings(self.path)
        got = []
        s.subscribe(lambda k, v: got.append((k, v)))
        self.assertTrue(s.set("always_on_top", True))
        self.assertFalse(s.set("always_on_top", True))
        self.assertEqual(got, [("always_on_top", True)])

    def test_unknown_key_rejected(self):
        with self.assertRaises(KeyError):
            S.Settings(self.path).set("nope", 1)

    def test_a_broken_listener_does_not_break_saving(self):
        s = S.Settings(self.path)
        s.subscribe(lambda k, v: 1 / 0)
        s.set("theme", "emerald")
        self.assertEqual(json.loads(self.path.read_text())["theme"], "emerald")


class LocaleTests(unittest.TestCase):
    def load(self, code):
        return json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))

    def test_en_and_ru_have_identical_keys_and_placeholders(self):
        en, ru = self.load("en"), self.load("ru")
        self.assertEqual(set(en), set(ru))
        for k in en:
            if k == "_meta":
                continue
            self.assertEqual(set(PH.findall(en[k])), set(PH.findall(ru[k])), k)
            self.assertTrue(ru[k].strip(), k)

    def test_every_key_used_in_code_exists(self):
        en = self.load("en")
        used = set()
        for f in ROOT.rglob("*.py"):
            src = f.read_text(encoding="utf-8")
            used |= set(re.findall(r'\btr\(\s*"([^"{}]+)"', src))
            used |= set(re.findall(r'_say\(\s*"([^"]+)"', src))
        self.assertEqual(sorted(k for k in used if k not in en), [])

    def test_dynamic_key_families_complete(self):
        en = self.load("en")
        from valinfo.analytics.profile import TRAIT_KEYS, TRAIT_VARIANTS
        from valinfo.ui.theme import THEMES
        for k in TRAIT_KEYS:
            for v in TRAIT_VARIANTS:
                self.assertIn(f"trait.{k}.{v}", en)
        for k in THEMES:
            self.assertIn(f"theme.{k}", en)
        for m in MODES.values():
            self.assertIn(f"mode.{m.key}", en)
        for k in ("agent", "country", "player", "rank", "peak", "kd", "hs", "adr", "acs", "wr", "streak", "main", "lvl"):
            self.assertIn(f"col.{k}", en)
        for pre, items in (("class", ("elite", "dangerous", "average", "below", "weak")),
                           ("style", ("entry", "aggressive", "support", "fragger", "passive", "balanced", "unknown")),
                           ("conf", ("low", "medium", "high")), ("reliab", ("low", "medium", "high")),
                           ("radar", ("aim", "entry", "survival", "impact", "teamplay", "consistency")),
                           ("part", ("rank", "form", "agent", "map", "session")),
                           ("comp", ("no_controller", "no_initiator", "no_duelist", "no_sentinel", "triple_duelist", "double_sentinel"))):
            for it in items:
                self.assertIn(f"{pre}.{it}", en)
        for k in ("rank", "form", "comp"):
            for v in ("pos", "neg", "even"):
                self.assertIn(f"reason.{k}.{v}", en)
        for k in ("star_ally", "star_enemy", "weak_ally", "weak_enemy", "even", "map_side", "map_side.nodata"):
            self.assertIn(f"reason.{k}", en)
        for t in ("unfamiliar", "smurf"):
            for team in ("ally", "enemy"):
                self.assertIn(f"reason.{t}.{team}", en)
        for k in ("status.match", "status.init", "state.pregame", "state.ingame"):
            self.assertIn(k, en)

    def test_translator_fallback_and_formatting(self):
        tr = Translator()
        tr.set_language("ru")
        self.assertEqual(tr.tr("live.your_team"), "ТВОЯ КОМАНДА")
        self.assertEqual(tr.tr("analysis.loaded", done=3, total=10), "Данные игроков загружены: 3 из 10")
        self.assertEqual(tr.tr("no.such.key"), "no.such.key")
        self.assertEqual(tr.tr("analysis.loaded"), tr.tr("analysis.loaded"))          # нехватка параметров не падает
        tr.set_language("en")
        self.assertEqual(tr.tr("live.your_team"), "YOUR TEAM")

    def test_adding_a_language_needs_only_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "de.json").write_text(json.dumps(
                {"_meta": {"code": "de", "name": "Deutsch"}, "live.your_team": "DEIN TEAM"}), encoding="utf-8")
            tr = Translator(extra_dirs=[Path(d)])
            self.assertIn(("de", "Deutsch"), tr.languages())
            tr.set_language("de")
            self.assertEqual(tr.tr("live.your_team"), "DEIN TEAM")
            self.assertEqual(tr.tr("live.enemy_team"), "ENEMY TEAM")                  # нет ключа → английский

    def test_broken_language_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "xx.json").write_text("{ nope", encoding="utf-8")
            tr = Translator(extra_dirs=[Path(d)])
            self.assertNotIn("xx", [c for c, _ in tr.languages()])
            self.assertGreaterEqual(len(tr.languages()), 2)

    def test_auto_resolution(self):
        tr = Translator()
        self.assertEqual(tr.resolve("auto", "ru_RU"), "ru")
        self.assertEqual(tr.resolve("auto", "de_DE"), "en")
        self.assertEqual(tr.resolve("ru", ""), "ru")
        self.assertEqual(tr.resolve("zz", ""), "en")


class ModeTests(unittest.TestCase):
    def test_known_modes(self):
        self.assertEqual(mode_for("competitive").ruleset, "bomb13")
        self.assertEqual(mode_for("swiftplay").ruleset, "swift5")
        self.assertEqual(mode_for("unrated").ruleset, "bomb13")
        self.assertEqual(mode_for("DeathMatch").layout, FFA)
        self.assertIsNone(mode_for("spikerush").ruleset)

    def test_unknown_and_empty(self):
        self.assertEqual(mode_for("brand-new-mode").layout, TEAMS)
        self.assertEqual(mode_for("brand-new-mode").key, "unknown")
        self.assertEqual(mode_for(None).key, "unknown")
        self.assertEqual(mode_for("premier-seasonmatch").key, "premier")

    def test_layout_detection(self):
        ten_solo = [{"team": f"t{i}"} for i in range(10)]
        self.assertEqual(detect_layout(mode_for("deathmatch"), [{"team": "Blue"}] * 10), FFA)
        self.assertEqual(detect_layout(mode_for("brand-new"), ten_solo), FFA)          # у каждого своя команда
        self.assertEqual(detect_layout(mode_for("competitive"), [{"team": "Blue"}] * 5 + [{"team": "Red"}] * 5), TEAMS)
        self.assertEqual(detect_layout(mode_for("competitive"), [{"team": "Blue"}] * 5), TEAMS)   # прегейм: видна одна команда


if __name__ == "__main__":
    unittest.main()
