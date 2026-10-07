"""Сценарии CountryFetcher с подменённым Edge (без сети и без браузера)."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from valinfo.country import fetcher as F
from valinfo.models import CountryStatus as S

NAME, TAG = "Ivan", "EUW"
PAD = " Competitive ACS K/D ADR Headshot Win rate Matches " * 12
FLAG_PAGE = f'<html><head><title>Ivan#EUW</title></head><body>Ivan {PAD}<img src="https://trackercdn.com/cdn/flags/4x3/de.svg"></body></html>'
PRIVATE_PAGE = f"<html><head><title>Ivan#EUW</title></head><body>Ivan This profile is private {PAD}</body></html>"
NOFLAG_PAGE = f"<html><head><title>Ivan#EUW</title></head><body>Ivan {PAD}</body></html>"
CF_PAGE = '<html><head><title>Just a moment...</title></head><body>challenges.cloudflare.com noindex,nofollow</body></html>'
HOME_PAGE = f"<html><head><title>Valorant Tracker</title></head><body>Sign in {PAD}</body></html>"


class FakeAssets:
    def has_flag(self, code): return True
    def ensure_flag(self, code): return True


class FetcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        patches = [
            mock.patch.object(F, "COUNTRY_CACHE_FILE", root / "c.json"),
            mock.patch.object(F, "EDGE_PROFILES_DIR", root / "p"),
            mock.patch.object(F, "DEBUG_PAGES_DIR", root / "dbg"),
            mock.patch.object(F, "find_edge", return_value="edge.exe"),
            mock.patch.object(F.CountryFetcher, "_sleep", staticmethod(lambda *a: None)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.root = root

    def resolve(self, pages, name=NAME, tag=TAG):
        it = iter(pages)
        calls = []

        def fake_run(edge, url, profile, wait_ms):
            calls.append(wait_ms)
            page = next(it)
            if isinstance(page, Exception):
                raise page
            return page

        f = F.CountryFetcher(FakeAssets(), workers=1)
        with mock.patch.object(F, "run_edge", fake_run):
            res = f._resolve(name, tag, self.root / "slot", lambda: False)
        return res, calls, f

    def test_found_first_try(self):
        res, calls, _ = self.resolve([FLAG_PAGE])
        self.assertEqual((res.status, res.code, len(calls)), (S.FOUND, "DE", 1))

    def test_private_stops_immediately_not_after_5_attempts(self):
        res, calls, _ = self.resolve([PRIVATE_PAGE])
        self.assertEqual((res.status, len(calls)), (S.PRIVATE, 1))

    def test_no_country_confirmed_after_two_loads_with_more_time(self):
        res, calls, _ = self.resolve([NOFLAG_PAGE, NOFLAG_PAGE])
        self.assertEqual((res.status, len(calls)), (S.NO_COUNTRY, 2))
        self.assertGreater(calls[1], calls[0])

    def test_late_flag_is_not_mistaken_for_no_country(self):
        res, calls, _ = self.resolve([NOFLAG_PAGE, FLAG_PAGE])
        self.assertEqual((res.status, res.code, len(calls)), (S.FOUND, "DE", 2))

    def test_cloudflare_then_success(self):
        res, calls, _ = self.resolve([CF_PAGE, CF_PAGE, FLAG_PAGE])
        self.assertEqual((res.status, len(calls)), (S.FOUND, 3))

    def test_cloudflare_every_time_is_blocked_failure(self):
        res, calls, _ = self.resolve([CF_PAGE] * F.COUNTRY_ATTEMPTS)
        self.assertEqual((res.status, len(calls)), (S.BLOCKED, F.COUNTRY_ATTEMPTS))
        self.assertTrue(res.status.is_failure)

    def test_wrong_page_every_time(self):
        res, _, _ = self.resolve([HOME_PAGE] * F.COUNTRY_ATTEMPTS)
        self.assertEqual(res.status, S.BAD_PAGE)
        self.assertIn("Unexpected page", res.detail)

    def test_edge_errors(self):
        res, _, _ = self.resolve([RuntimeError("Edge timed out (35s)")] * F.COUNTRY_ATTEMPTS)
        self.assertEqual(res.status, S.ERROR)
        self.assertIn("timed out", res.detail)

    def test_mixed_failure_then_private(self):
        res, calls, _ = self.resolve([CF_PAGE, HOME_PAGE, PRIVATE_PAGE])
        self.assertEqual((res.status, len(calls)), (S.PRIVATE, 3))

    def test_one_unconfirmed_no_flag_plus_blocks_is_failure_not_no_country(self):
        res, _, _ = self.resolve([NOFLAG_PAGE] + [CF_PAGE] * (F.COUNTRY_ATTEMPTS - 1))
        self.assertEqual(res.status, S.BLOCKED)
        self.assertIn("could not be confirmed", res.detail)

    def test_results_cached_and_failures_not(self):
        _, _, f = self.resolve([PRIVATE_PAGE])
        self.assertEqual(f.lookup(NAME, TAG).status, S.PRIVATE)
        _, _, f2 = self.resolve([CF_PAGE] * F.COUNTRY_ATTEMPTS, name="Other")
        self.assertIsNone(f2.lookup("Other", TAG))

    def test_no_edge(self):
        with mock.patch.object(F, "find_edge", return_value=None):
            f = F.CountryFetcher(FakeAssets(), workers=1)
            res = f._resolve(NAME, TAG, self.root / "s", lambda: False)
        self.assertEqual(res.status, S.ERROR)
        self.assertIn("Edge not found", res.detail)

    def test_cancel_gives_interrupted(self):
        f = F.CountryFetcher(FakeAssets(), workers=1)
        res = f._resolve(NAME, TAG, self.root / "s", lambda: True)
        self.assertEqual(res.status, S.INTERRUPTED)

    def test_cache_hit_answers_synchronously_without_queue(self):
        _, _, f = self.resolve([FLAG_PAGE])
        got = []
        f.submit(NAME, TAG, lambda: False, got.append)
        self.assertEqual(got[0].code, "DE")           # вызван сразу, воркеры даже не стартовали
        self.assertFalse(f._started)

    def test_submit_runs_through_worker_thread(self):
        f = F.CountryFetcher(FakeAssets(), workers=1)
        done = threading.Event()
        out = []
        with mock.patch.object(F, "run_edge", lambda *a: PRIVATE_PAGE):
            f.submit("New", "1", lambda: False, lambda r: (out.append(r), done.set()))
            self.assertTrue(done.wait(5))
        self.assertEqual(out[0].status, S.PRIVATE)

    def test_debug_dump_written_and_capped(self):
        with mock.patch.object(F, "COUNTRY_DEBUG_DUMP_KEEP", 3):
            for i in range(6):
                self.resolve([HOME_PAGE] * F.COUNTRY_ATTEMPTS, name=f"P{i}")
        self.assertLessEqual(len(list((self.root / "dbg").glob("*.html"))), 3)


if __name__ == "__main__":
    unittest.main()
