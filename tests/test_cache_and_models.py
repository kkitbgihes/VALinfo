import json
import tempfile
import unittest
from pathlib import Path

from valinfo.country.cache import CountryCache, make_key
from valinfo.models import CountryResult, CountryStatus as S


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "countries.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_legacy_format_is_read_as_found(self):
        self.path.write_text(json.dumps({"nick#tag": "ru"}), encoding="utf-8")
        c = CountryCache(self.path, 24)
        self.assertEqual(c.get("nick#tag"), CountryResult(S.FOUND, "RU"))

    def test_found_is_forever(self):
        c = CountryCache(self.path, 1)
        c.put("a#b", CountryResult(S.FOUND, "DE"), now=0)
        self.assertEqual(c.get("a#b", now=10 ** 9).code, "DE")

    def test_negative_expires(self):
        c = CountryCache(self.path, 1)
        c.put("a#b", CountryResult(S.PRIVATE, detail="x"), now=1000)
        self.assertEqual(c.get("a#b", now=1000 + 3599).status, S.PRIVATE)
        self.assertIsNone(c.get("a#b", now=1000 + 3601))

    def test_failures_are_never_cached(self):
        c = CountryCache(self.path, 24)
        for st in (S.BLOCKED, S.BAD_PAGE, S.ERROR, S.INTERRUPTED):
            c.put("a#b", CountryResult(st, detail="x"))
        self.assertIsNone(c.get("a#b"))
        self.assertFalse(self.path.exists())

    def test_persists_between_instances(self):
        CountryCache(self.path, 24).put("a#b", CountryResult(S.NO_COUNTRY))
        self.assertEqual(CountryCache(self.path, 24).get("a#b").status, S.NO_COUNTRY)

    def test_corrupt_file_ignored(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(CountryCache(self.path, 24).get("a#b"))

    def test_key_is_case_insensitive(self):
        self.assertEqual(make_key("Nick", "TAG"), "nick#tag")


class ModelTests(unittest.TestCase):
    def test_roundtrip(self):
        r = CountryResult(S.FOUND, "UA", "d")
        self.assertEqual(CountryResult.from_dict(r.to_dict()), r)

    def test_from_garbage(self):
        self.assertEqual(CountryResult.from_dict(None).status, S.SKIPPED)
        self.assertEqual(CountryResult.from_dict({"status": "wat"}).status, S.SKIPPED)

    def test_failure_flags(self):
        self.assertTrue(S.BLOCKED.is_failure and S.BAD_PAGE.is_failure and S.ERROR.is_failure)
        self.assertFalse(S.PRIVATE.is_failure or S.NO_COUNTRY.is_failure)


if __name__ == "__main__":
    unittest.main()
