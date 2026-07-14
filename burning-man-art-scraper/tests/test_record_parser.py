from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.preview import write_first_record_preview
from burning_man_scraper.record_parser import parse_installation_record


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def detail_result(url: str, fixture_name: str) -> FetchResult:
    body = (FIXTURES / fixture_name).read_bytes()
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        fetched_timestamp="2026-06-17T00:00:00+00:00",
        content_type="text/html",
        response_hash=fixture_name,
        etag=None,
        last_modified=None,
        body=body,
    )


class RecordParserTests(unittest.TestCase):
    def test_standard_installation(self):
        url = "https://history.burningman.org/art-history/installation/temple-of-dust/"

        preview = parse_installation_record(
            detail_result(url, "standard_installation.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=1,
            scrape_run_id="preview-run",
        )

        record = preview.record
        self.assertEqual(record.title, "Temple of Dust")
        self.assertEqual(record.normalized_title, "temple of dust")
        self.assertEqual(record.artist_names, ["Avery Stone"])
        self.assertEqual(record.primary_image_url, "https://history.burningman.org/images/temple.jpg")
        self.assertEqual(record.photographer_credit, "Photo by Dana Lens")
        self.assertEqual(record.website_url, "https://artist.example/temple")
        self.assertNotIn("https://donate.burningman.org/project/temple-of-dust", record.external_links)
        self.assertFalse(record.parsing_errors)

    def test_multiple_artists(self):
        url = "https://history.burningman.org/art-history/installation/many-hands/"

        preview = parse_installation_record(
            detail_result(url, "multiple_artists.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=1,
        )

        self.assertEqual(preview.record.artist_names, ["Lena Ray", "Omar Vale", "Priya Sen"])

    def test_collective(self):
        url = "https://history.burningman.org/art-history/installation/signal-tower/"

        preview = parse_installation_record(
            detail_result(url, "collective.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=1,
        )

        self.assertEqual(preview.record.artist_collective, "Desert Signal Collective")

    def test_missing_artist(self):
        url = "https://history.burningman.org/art-history/installation/untethered/"

        preview = parse_installation_record(
            detail_result(url, "missing_artist.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=1,
        )

        self.assertIsNone(preview.record.artist_display_text)
        self.assertEqual(preview.record.artist_names, [])
        self.assertIn("artist_display_text", preview.record.missing_fields)

    def test_missing_image(self):
        url = "https://history.burningman.org/art-history/installation/no-image-work/"

        preview = parse_installation_record(
            detail_result(url, "missing_image.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=1,
        )

        self.assertEqual(preview.record.image_urls, [])
        self.assertIsNone(preview.record.primary_image_url)
        self.assertIn("primary_image_url", preview.record.missing_fields)

    def test_missing_photographer(self):
        url = "https://history.burningman.org/art-history/installation/no-credit-work/"

        preview = parse_installation_record(
            detail_result(url, "missing_photographer.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=1,
        )

        self.assertIsNone(preview.record.photographer_credit)
        self.assertIn("photographer_credit", preview.record.missing_fields)

    def test_malformed_record(self):
        url = "https://history.burningman.org/art-history/installation/malformed-record/"

        preview = parse_installation_record(
            detail_result(url, "malformed_record.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=1,
        )

        self.assertIsNone(preview.record.title)
        self.assertIn("Missing required preview title.", preview.record.parsing_errors)

    def test_preview_files_are_saved(self):
        url = "https://history.burningman.org/art-history/installation/temple-of-dust/"
        preview = parse_installation_record(
            detail_result(url, "standard_installation.html"),
            source_archive_url="https://history.burningman.org/art-history/archive/?yyyy=2022",
            source_position=3,
            scrape_run_id="preview-run",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, csv_path, md_path = write_first_record_preview(preview, Path(temp_dir))
            payload = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertTrue(csv_path.exists())
            self.assertTrue(md_path.exists())
            self.assertEqual(payload["title"], "Temple of Dust")


if __name__ == "__main__":
    unittest.main()
