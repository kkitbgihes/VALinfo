import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from valinfo.analytics.post_match import parse_ultra_post_match
from valinfo.api import static as ST


class PostMatchTests(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertIsNone(parse_ultra_post_match({}, {}, {}, "me"))
        self.assertIsNone(parse_ultra_post_match(None, {}, {}, "me"))


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(ST, "STATIC_CACHE_FILE", Path(self.tmp.name) / "static.json")
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def fake_fetch(self, fail=()):
        data = {
            "version": {"riotClientVersion": "release-1.0"},
            "agents?isPlayableCharacter=true": [{"uuid": "AAA", "displayName": "Jett", "displayIcon": "u", "role": {"displayName": "Duelist"}}],
            "seasons": [{"uuid": "S1", "type": "EAresSeasonType::Act", "startTime": "2023-01-01"},
                        {"uuid": "OLD", "type": "EAresSeasonType::Act", "startTime": "2019-01-01"}],
            "maps": [{"mapUrl": "/Game/Maps/Ascent", "displayName": "Ascent"}],
            "competitivetiers": [{"tiers": [{"tier": 3, "smallIcon": "i"}]}],
        }

        def f(path):
            self.calls.append(path)
            if path in fail:
                raise RuntimeError("net down")
            return data[path]
        return f

    def test_cold_start_fetches_everything_and_caches(self):
        with mock.patch.object(ST, "_fetch", self.fake_fetch()):
            sd = ST.load_static()
        self.assertEqual(len(self.calls), 5)
        self.assertEqual((sd.version, sd.agents, sd.seasons, sd.maps), ("release-1.0", {"aaa": "Jett"}, {"s1"}, {"/game/maps/ascent": "Ascent"}))
        self.assertEqual(sd.tier_icons, {3: "i"})
        self.assertEqual(sd.agent_roles, {"aaa": "Duelist"})                   # роли агентов (для композиции команд)
        self.assertEqual(sd.season_dates, {"s1": "2023-01-01", "old": "2019-01-01"})   # даты актов (для даты пика)

    def test_old_cache_without_roles_is_refetched(self):
        with mock.patch.object(ST, "_fetch", self.fake_fetch()):
            ST.load_static()
        raw = json.loads(ST.STATIC_CACHE_FILE.read_text())
        raw.pop("agent_roles"); raw.pop("season_dates")                          # кэш старой версии программы
        ST.STATIC_CACHE_FILE.write_text(json.dumps(raw))
        self.calls.clear()
        with mock.patch.object(ST, "_fetch", self.fake_fetch()):
            sd = ST.load_static()
        self.assertEqual(sd.agent_roles, {"aaa": "Duelist"})
        self.assertGreater(len(self.calls), 1)

    def test_warm_start_only_asks_version(self):
        with mock.patch.object(ST, "_fetch", self.fake_fetch()):
            ST.load_static()
        self.calls.clear()
        with mock.patch.object(ST, "_fetch", self.fake_fetch()):
            sd = ST.load_static()
        self.assertEqual(self.calls, ["version"])
        self.assertEqual(sd.tier_icons, {3: "i"})               # int-ключи восстановлены из JSON
        self.assertEqual(sd.agent_icons, {"aaa": "u"})

    def test_offline_with_cache_still_starts(self):
        with mock.patch.object(ST, "_fetch", self.fake_fetch()):
            ST.load_static()
        every = ("version", "agents?isPlayableCharacter=true", "seasons", "maps", "competitivetiers")
        raw = json.loads(ST.STATIC_CACHE_FILE.read_text())
        raw["ts"] = time.time() - 10 ** 7                       # протухший кэш
        ST.STATIC_CACHE_FILE.write_text(json.dumps(raw))
        with mock.patch.object(ST, "_fetch", self.fake_fetch(fail=every)):
            sd = ST.load_static()
        self.assertEqual(sd.agents, {"aaa": "Jett"})

    def test_offline_without_cache_raises(self):
        with mock.patch.object(ST, "_fetch", self.fake_fetch(fail=("version",))):
            with self.assertRaises(RuntimeError):
                ST.load_static()


if __name__ == "__main__":
    unittest.main()
