"""Unit tests: normalize without destroying meaning."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.sources.artist_website.ingest import (
    extract_dimensions,
    normalize_title,
    normalize_url,
)
from burning_man_scraper.sources.artist_website.text_normalize import (
    normalize_display_text,
    normalize_dimension_text,
    normalize_identity_url,
    normalize_match_key,
    title_from_slug_words,
)
from burning_man_scraper.url_utils import (
    TRACKING_PARAMETERS as BM_TRACKING,
    normalize_url as bm_normalize_url,
    validate_archive_url,
)


class DisplayTextTests(unittest.TestCase):
    def test_preserves_accents_and_case(self):
        self.assertEqual(normalize_display_text("  Café  Néon  "), "Café Néon")
        self.assertEqual(normalize_display_text("Étude Nº 3"), "Étude Nº 3")

    def test_smart_quotes_and_entities(self):
        self.assertEqual(normalize_display_text("“Sunrise Wall”"), '"Sunrise Wall"')
        self.assertEqual(normalize_display_text("Art &amp; Light"), "Art & Light")
        self.assertEqual(normalize_display_text("itâ€™s fine"), "it's fine")

    def test_match_key_folds_without_writing_display(self):
        display = normalize_display_text("Café Néon")
        key = normalize_match_key(display)
        self.assertEqual(display, "Café Néon")
        self.assertEqual(key, "cafe neon")
        self.assertNotEqual(display, key)
        # Public alias used for dedup must not equal display
        self.assertEqual(normalize_title("Café Néon"), "cafe neon")
        self.assertNotEqual(normalize_title("Café Néon"), "Café Néon")

    def test_slug_words_do_not_invent_title_case(self):
        self.assertEqual(title_from_slug_words("in the light"), "in the light")
        self.assertEqual(title_from_slug_words("WATER FINDS A WAY"), "WATER FINDS A WAY")


class DimensionNormalizeTests(unittest.TestCase):
    def test_multiplication_sign_and_mojibake(self):
        self.assertIn("×", normalize_dimension_text("18 x 24 in"))
        self.assertIn("×", normalize_dimension_text("12 Ã— 4 ft"))
        dims = extract_dimensions("Neon installation 12 Ã— 4 ft")
        self.assertTrue(dims)
        self.assertRegex(dims, r"12")
        self.assertRegex(dims, r"4")


class IdentityUrlTests(unittest.TestCase):
    def test_utm_slash_fragment_identity(self):
        a = normalize_identity_url(
            "https://Example.com/artworks/281-work/?utm_source=x&fbclid=1&keep=1#section"
        )
        b = normalize_identity_url("https://example.com/artworks/281-work?keep=1")
        self.assertEqual(a, b)
        self.assertEqual(a, "https://example.com/artworks/281-work?keep=1")

    def test_ingest_and_discover_agree(self):
        from burning_man_scraper.sources.artist_website.discover import normalize_detail_url

        url = "https://www.example.com/work/echo/?utm_campaign=x&gclid=1#frag"
        self.assertEqual(normalize_url(url), normalize_detail_url(url))
        self.assertEqual(normalize_url(url), normalize_identity_url(url))

    def test_www_vs_bare_kept_distinct_for_identity(self):
        www = normalize_identity_url("https://www.example.com/work/a")
        bare = normalize_identity_url("https://example.com/work/a")
        self.assertNotEqual(www, bare)

    def test_bm_archive_tracking_unchanged(self):
        url = (
            "https://history.burningman.org/art-history/archive/"
            "?utm_source=chatgpt.com&fbclid=abc&gclid=xyz&yyyy=2022"
        )
        self.assertEqual(
            bm_normalize_url(url),
            "https://history.burningman.org/art-history/archive/?yyyy=2022",
        )
        self.assertIn("utm_source", BM_TRACKING)
        self.assertEqual(
            validate_archive_url(
                "https://history.burningman.org/art-history/archive/?yyyy=2022"
            ),
            "https://history.burningman.org/art-history/archive/?yyyy=2022",
        )


if __name__ == "__main__":
    unittest.main()
