from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.cli import build_first_record_preview
from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.inline_archive import extract_inline_archive_records
from burning_man_scraper.inspection import inspect_html
from burning_man_scraper.state import ScraperState


FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIVE_URL = "https://history.burningman.org/art-history/archive/?yyyy=2022"


def inline_archive_result() -> FetchResult:
    return FetchResult(
        requested_url=ARCHIVE_URL,
        final_url=ARCHIVE_URL,
        status_code=200,
        fetched_timestamp="2026-06-17T00:00:00+00:00",
        content_type="text/html",
        response_hash="inline-archive",
        etag=None,
        last_modified=None,
        body=(FIXTURES / "archive_inline_2022.html").read_bytes(),
    )


class InlineArchiveTests(unittest.TestCase):
    def test_extracts_inline_archive_records_in_source_order(self):
        records = extract_inline_archive_records(
            (FIXTURES / "archive_inline_2022.html").read_text(encoding="utf-8"),
            archive_url=ARCHIVE_URL,
            final_url=ARCHIVE_URL,
        )

        self.assertEqual([record.title for record in records], [
            "... the world is Watching.",
            "1:44 Inter-dimensional Space Time Portal",
            "38",
        ])
        self.assertEqual(records[1].artist_display_text, "Harlan Emil Gruber and Maraya - TransPortals")
        self.assertEqual(records[1].artist_location, "Taos, NM")
        self.assertEqual(records[1].website_url, "https://transportals.org/144-inter-dimensional-space-time-portal/")
        self.assertNotIn("harlanemil@gmail.com", records[1].description)
        self.assertNotIn("https://transportals.org", records[1].description)
        self.assertNotIn("Donate To This Project", records[0].description)
        self.assertIsNone(records[0].website_url)

    def test_inspection_uses_inline_fragment_candidates(self):
        inspection = inspect_html(
            entered_url=ARCHIVE_URL,
            normalized_url=ARCHIVE_URL,
            fetch_result=inline_archive_result(),
            robots_result=None,
        )

        self.assertEqual(len(inspection.candidate_installation_links), 3)
        self.assertEqual(
            inspection.candidate_installation_links[0],
            "https://history.burningman.org/art-history/archive/?yyyy=2022#a2I8X00000first",
        )

    def test_donate_links_are_ignored(self):
        inspection = inspect_html(
            entered_url=ARCHIVE_URL,
            normalized_url=ARCHIVE_URL,
            fetch_result=inline_archive_result(),
            robots_result=None,
        )
        excluded = {link.url: link.reason for link in inspection.excluded_links}

        self.assertEqual(
            excluded["https://donate.burningman.org/project/world-watching"],
            "donate_link_ignored",
        )
        self.assertEqual(
            excluded["https://history.burningman.org/donate-to-this-project/?id=a2I8X00000h85IfUAI"],
            "donate_link_ignored",
        )
        self.assertNotIn(
            "https://history.burningman.org/donate-to-this-project/?id=a2I8X00000h85IfUAI",
            inspection.candidate_installation_links,
        )

    def test_preview_parses_inline_first_record_without_detail_fetch(self):
        class NoDetailFetcher:
            def __init__(self):
                self.requested_urls: list[str] = []

            def fetch(self, url: str, allowed_urls: set[str]):
                self.requested_urls.append(url)
                raise AssertionError("Inline archive preview must not fetch fragment candidates.")

        with tempfile.TemporaryDirectory() as temp_dir:
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            source_lookup = state.get_or_create_source(ARCHIVE_URL, ARCHIVE_URL)
            inspection = inspect_html(
                entered_url=ARCHIVE_URL,
                normalized_url=ARCHIVE_URL,
                fetch_result=inline_archive_result(),
                robots_result=None,
            )
            fetcher = NoDetailFetcher()

            preview = build_first_record_preview(
                inspection=inspection,
                source_lookup=source_lookup,
                source_result=inline_archive_result(),
                fetcher=fetcher,  # type: ignore[arg-type]
                state_store=state,
                preview_run_id="preview-run",
                output_func=lambda _message: None,
            )

        self.assertIsNotNone(preview)
        self.assertEqual(preview.record.title, "... the world is Watching.")
        self.assertEqual(preview.record.artist_display_text, "Eric Tussey a.k.a. pebble")
        self.assertEqual(preview.record.artist_location, "Boulder, CO")
        self.assertNotIn("eric@tussey.com", preview.record.description)
        self.assertEqual(fetcher.requested_urls, [])


if __name__ == "__main__":
    unittest.main()
