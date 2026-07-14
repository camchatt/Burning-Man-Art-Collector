from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.url_utils import normalize_url, validate_archive_url


class UrlUtilsTests(unittest.TestCase):
    def test_valid_archive_url_normalizes_successfully(self):
        url = "https://history.burningman.org/art-history/archive/"

        self.assertEqual(validate_archive_url(url), url)

    def test_tracking_parameters_are_removed(self):
        url = (
            "https://history.burningman.org/art-history/archive/"
            "?utm_source=chatgpt.com&fbclid=abc&gclid=xyz&yyyy=2022"
        )

        self.assertEqual(
            normalize_url(url),
            "https://history.burningman.org/art-history/archive/?yyyy=2022",
        )

    def test_yyyy_parameter_is_preserved(self):
        url = "https://history.burningman.org/art-history/archive/?utm_medium=test&yyyy=2022"

        self.assertEqual(
            normalize_url(url),
            "https://history.burningman.org/art-history/archive/?yyyy=2022",
        )

    def test_query_parameters_are_sorted_consistently(self):
        url = "https://history.burningman.org/art-history/archive/?page=2&category=art&yyyy=2022"

        self.assertEqual(
            normalize_url(url),
            "https://history.burningman.org/art-history/archive/?category=art&page=2&yyyy=2022",
        )

    def test_invalid_hostname_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "hostname must be history.burningman.org"):
            validate_archive_url("https://example.com/art-history/archive/")

    def test_empty_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            validate_archive_url("")


if __name__ == "__main__":
    unittest.main()
