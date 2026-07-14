from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.config import ScraperConfig
from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.inspection import inspect_html
from burning_man_scraper.preview import write_raw_html, write_source_manifest


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fetch_result(url: str, fixture_name: str) -> FetchResult:
    body = (FIXTURES / fixture_name).read_bytes()
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        fetched_timestamp="2026-06-17T00:00:00+00:00",
        content_type="text/html; charset=utf-8",
        response_hash="fixture-hash",
        etag='"abc"',
        last_modified="Wed, 17 Jun 2026 00:00:00 GMT",
        body=body,
    )


def robots_result(status_code: int = 200) -> FetchResult:
    return FetchResult(
        requested_url="https://history.burningman.org/robots.txt",
        final_url="https://history.burningman.org/robots.txt",
        status_code=status_code,
        fetched_timestamp="2026-06-17T00:00:00+00:00",
        content_type="text/plain",
        response_hash="robots-hash",
        etag=None,
        last_modified=None,
        body=b"User-agent: *\n",
    )


class InspectionTests(unittest.TestCase):
    def test_archive_page_detection(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"

        inspection = inspect_html(url, url, fetch_result(url, "archive_2022.html"), robots_result())

        self.assertEqual(inspection.detected_page_type, "filtered archive listing page")
        self.assertEqual(inspection.detected_year, "2022")
        self.assertEqual(inspection.page_title, "Burning Man 2022 Art Archive")

    def test_installation_detail_page_detection(self):
        url = "https://history.burningman.org/art-history/installation/first-installation/"

        inspection = inspect_html(url, url, fetch_result(url, "installation_detail.html"), robots_result())

        self.assertEqual(inspection.detected_page_type, "single installation detail page")

    def test_candidate_link_extraction(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"

        inspection = inspect_html(url, url, fetch_result(url, "archive_2022.html"), robots_result())

        self.assertEqual(
            inspection.candidate_installation_links,
            [
                "https://history.burningman.org/art-history/installation/malformed-record/",
                "https://history.burningman.org/art-history/installation/temple-of-dust/",
            ],
        )

    def test_unrelated_link_exclusion(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"

        inspection = inspect_html(url, url, fetch_result(url, "archive_2022.html"), robots_result())
        reasons = {link.url: link.reason for link in inspection.excluded_links}

        self.assertIn("https://example.com/story", reasons)
        self.assertEqual(reasons["https://example.com/story"], "external_website")
        self.assertIn("https://history.burningman.org/about/", reasons)
        self.assertEqual(reasons["https://history.burningman.org/about/"], "internal_non_installation_link")

    def test_no_pagination_traversal(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"

        inspection = inspect_html(url, url, fetch_result(url, "archive_2022.html"), robots_result())

        self.assertTrue(inspection.pagination_detected)
        self.assertNotIn(
            "https://history.burningman.org/art-history/archive/?page=2&yyyy=2022",
            inspection.candidate_installation_links,
        )

    def test_raw_html_storage(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        result = fetch_result(url, "archive_2022.html")

        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, metadata_path = write_raw_html(result, Path(temp_dir))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertTrue(html_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertEqual(metadata["requested_url"], url)
            self.assertEqual(metadata["status_code"], 200)
            self.assertEqual(metadata["etag"], '"abc"')
            self.assertEqual(metadata["last_modified"], "Wed, 17 Jun 2026 00:00:00 GMT")

    def test_robots_txt_handling(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"

        inspection = inspect_html(url, url, fetch_result(url, "archive_2022.html"), robots_result(404))

        self.assertEqual(inspection.robots_txt_status, "404 https://history.burningman.org/robots.txt")

    def test_source_manifest_generation(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        inspection = inspect_html(url, url, fetch_result(url, "archive_2022.html"), robots_result())

        with tempfile.TemporaryDirectory() as temp_dir:
            config = ScraperConfig(
                preview_manifest_path=Path(temp_dir) / "source_manifest.json",
                raw_html_dir=Path(temp_dir) / "raw_html",
                request_delay_seconds=0,
            )
            manifest_path = write_source_manifest(inspection, requested_count=2, config=config)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["supplied_source_url"], url)
            self.assertFalse(manifest["pagination_authorized"])
            self.assertEqual(manifest["candidate_installation_count"], 2)
            self.assertEqual(manifest["proposed_request_count"], 2)


if __name__ == "__main__":
    unittest.main()
