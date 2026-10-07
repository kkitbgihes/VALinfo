import unittest

from valinfo.country.classify import classify_page, find_country, is_cloudflare
from valinfo.models import CountryStatus as S

NAME, TAG = "Ivan Ivanov", "EUW"
FLAG = '<img src="https://trackercdn.com/cdn/flags/4x3/ua.svg">'


def page(body, title="Ivan Ivanov#EUW - Valorant Tracker", scripts=""):
    return f"<html><head><title>{title}</title></head><body>{body}<script>{scripts}</script></body></html>"


PAD = " Competitive Unrated ACS K/D ADR Headshot Win rate Matches Damage/Round " * 10


class ClassifyTests(unittest.TestCase):
    def test_flag_found(self):
        v = classify_page(page(f"Ivan Ivanov {FLAG} {PAD}"), NAME, TAG)
        self.assertEqual((v.status, v.code), (S.FOUND, "UA"))

    def test_flag_wins_even_on_odd_page(self):          # поведение оригинала: флаг — главный признак
        v = classify_page("<html>" + FLAG + "</html>", NAME, TAG)
        self.assertEqual(v.status, S.FOUND)

    def test_cloudflare_title(self):
        html = '<html><head><title>Just a moment...</title></head><body>challenges.cloudflare.com noindex,nofollow</body></html>'
        self.assertTrue(is_cloudflare(html))
        self.assertEqual(classify_page(html, NAME, TAG).status, S.BLOCKED)

    def test_cloudflare_russian_title(self):
        html = '<html><head><title>Один момент…</title></head><body></body></html>'
        self.assertEqual(classify_page(html, NAME, TAG).status, S.BLOCKED)

    def test_access_denied(self):
        self.assertEqual(classify_page(page("x", title="Access denied"), NAME, TAG).status, S.BLOCKED)

    def test_private_english(self):
        v = classify_page(page(f"<h1>Ivan Ivanov</h1><p>This profile is private.</p>{PAD}"), NAME, TAG)
        self.assertEqual(v.status, S.PRIVATE)

    def test_private_russian(self):
        v = classify_page(page(f"<h1>Ivan Ivanov</h1><p>Профиль скрыт</p>{PAD}"), NAME, TAG)
        self.assertEqual(v.status, S.PRIVATE)

    def test_private_raw_marker_in_state(self):
        v = classify_page(page(f"Ivan Ivanov {PAD}", scripts='{"code":"CollectorResultStatus::Private"}'), NAME, TAG)
        self.assertEqual(v.status, S.PRIVATE)

    def test_make_private_link_is_not_private(self):   # «Make private» из подвала tracker.gg не должно срабатывать
        v = classify_page(page(f"Ivan Ivanov. Your profile becomes public. Make private. {PAD}"), NAME, TAG)
        self.assertEqual(v.status, S.NO_COUNTRY)

    def test_close_button_is_not_private(self):
        v = classify_page(page(f"Ivan Ivanov Профиль игрока ... Закрыть {PAD}"), NAME, TAG)
        self.assertEqual(v.status, S.NO_COUNTRY)

    def test_private_word_inside_script_ignored(self):
        v = classify_page(page(f"Ivan Ivanov {PAD}", scripts='var s="this profile is private";'), NAME, TAG)
        self.assertEqual(v.status, S.NO_COUNTRY)

    def test_no_country(self):
        v = classify_page(page(f"<h1>Ivan Ivanov</h1>{PAD}"), NAME, TAG)
        self.assertEqual(v.status, S.NO_COUNTRY)

    def test_name_with_entities(self):
        v = classify_page(page(f"<h1>Tom &amp; Jerry</h1>{PAD}", title="Tom &amp; Jerry#1"), "Tom & Jerry", "1")
        self.assertEqual(v.status, S.NO_COUNTRY)

    def test_unicode_name_case_insensitive(self):
        v = classify_page(page(f"<h1>ИВАН</h1>{PAD}"), "иван", "1")
        self.assertEqual(v.status, S.NO_COUNTRY)

    def test_wrong_page_homepage(self):
        v = classify_page(page("Valorant Tracker home. Sign in with Riot ID. " + PAD, title="Valorant Tracker"), NAME, TAG)
        self.assertEqual(v.status, S.BAD_PAGE)
        self.assertIn("Unexpected page", v.detail)

    def test_not_found_page(self):
        v = classify_page(page(f"Player Ivan Ivanov#EUW not found. {PAD}"), NAME, TAG)
        self.assertEqual(v.status, S.BAD_PAGE)

    def test_empty_page(self):
        v = classify_page("<html><head></head><body></body></html>", NAME, TAG)
        self.assertEqual(v.status, S.BAD_PAGE)

    def test_find_country_upper(self):
        self.assertEqual(find_country(FLAG), "UA")
        self.assertIsNone(find_country("<html></html>"))


if __name__ == "__main__":
    unittest.main()
