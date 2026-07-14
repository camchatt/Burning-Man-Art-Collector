from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.cli import (
    build_first_record_preview,
    prompt_for_overwrite_batch,
    prompt_for_record_count,
    run_interactive,
)
from burning_man_scraper.config import ScraperConfig
from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.inspection import PageInspection
from burning_man_scraper.state import ScraperState


class FakeFetcher:
    def __init__(self):
        self.requested_urls: list[str] = []

    def fetch_source_and_robots(self, source_url: str):
        self.requested_urls.extend(
            [
                "https://history.burningman.org/robots.txt",
                source_url,
            ]
        )
        source_result = FetchResult(
            requested_url=source_url,
            final_url=source_url,
            status_code=200,
            fetched_timestamp="2026-06-17T00:00:00+00:00",
            content_type="text/html",
            response_hash="hash",
            etag=None,
            last_modified=None,
            body=b"<html><head><title>Archive</title></head><body></body></html>",
        )
        robots_result = FetchResult(
            requested_url="https://history.burningman.org/robots.txt",
            final_url="https://history.burningman.org/robots.txt",
            status_code=200,
            fetched_timestamp="2026-06-17T00:00:00+00:00",
            content_type="text/plain",
            response_hash="robots-hash",
            etag=None,
            last_modified=None,
            body=b"User-agent: *\n",
        )
        return source_result, robots_result


class ArchiveFakeFetcher(FakeFetcher):
    def __init__(self):
        super().__init__()
        self.fixture_by_url = {
            "https://history.burningman.org/art-history/installation/malformed-record/": "malformed_record.html",
            "https://history.burningman.org/art-history/installation/temple-of-dust/": "standard_installation.html",
        }

    def fetch_source_and_robots(self, source_url: str):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "archive_2022.html"
        self.requested_urls.extend(
            [
                "https://history.burningman.org/robots.txt",
                source_url,
            ]
        )
        source_result = FetchResult(
            requested_url=source_url,
            final_url=source_url,
            status_code=200,
            fetched_timestamp="2026-06-17T00:00:00+00:00",
            content_type="text/html",
            response_hash="hash",
            etag=None,
            last_modified=None,
            body=fixture_path.read_bytes(),
        )
        robots_result = FetchResult(
            requested_url="https://history.burningman.org/robots.txt",
            final_url="https://history.burningman.org/robots.txt",
            status_code=200,
            fetched_timestamp="2026-06-17T00:00:00+00:00",
            content_type="text/plain",
            response_hash="robots-hash",
            etag=None,
            last_modified=None,
            body=b"User-agent: *\n",
        )
        return source_result, robots_result

    def fetch(self, url: str, allowed_urls: set[str]):
        if url not in allowed_urls:
            raise ValueError(f"outside boundary: {url}")
        self.requested_urls.append(url)
        fixture_name = self.fixture_by_url[url]
        fixture_path = Path(__file__).resolve().parent / "fixtures" / fixture_name
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            fetched_timestamp="2026-06-17T00:00:00+00:00",
            content_type="text/html",
            response_hash=fixture_name,
            etag=None,
            last_modified=None,
            body=fixture_path.read_bytes(),
        )


class InteractiveInputTests(unittest.TestCase):
    def test_invalid_record_count_reprompts_until_valid(self):
        inputs = iter(["0", "abc", "101", "5"])
        outputs: list[str] = []

        count = prompt_for_record_count(
            max_records=100,
            input_func=lambda _: next(inputs),
            output_func=outputs.append,
        )

        self.assertEqual(count, 5)
        self.assertTrue(any("positive integer" in output for output in outputs))
        self.assertTrue(any("maximum allowed is 100" in output for output in outputs))

    def test_empty_record_count_reprompts_until_valid(self):
        inputs = iter(["", "3"])
        outputs: list[str] = []

        count = prompt_for_record_count(
            max_records=100,
            input_func=lambda _: next(inputs),
            output_func=outputs.append,
        )

        self.assertEqual(count, 3)
        self.assertTrue(any("cannot be empty" in output for output in outputs))

    def test_interactive_run_prints_entered_normalized_url_and_count(self):
        inputs = iter(
            [
                "https://history.burningman.org/art-history/archive/?utm_source=chatgpt.com&yyyy=2022",
                "7",
            ]
        )
        outputs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            fetcher = FakeFetcher()
            exit_code = run_interactive(
                config=ScraperConfig(
                    max_records_per_run=10,
                    state_database_path=Path(temp_dir) / "scraper_state.sqlite3",
                    preview_manifest_path=Path(temp_dir) / "source_manifest.json",
                    raw_html_dir=Path(temp_dir) / "raw_html",
                    request_delay_seconds=0,
                ),
                fetcher=fetcher,
                input_func=lambda _: next(inputs),
                output_func=outputs.append,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "Entered URL: https://history.burningman.org/art-history/archive/?utm_source=chatgpt.com&yyyy=2022",
            outputs,
        )
        self.assertIn(
            "Normalized URL: https://history.burningman.org/art-history/archive/?yyyy=2022",
            outputs,
        )
        self.assertIn("Requested record count: 7", outputs)
        self.assertIn("SOURCE SUMMARY", outputs)
        self.assertIn("PAGE INSPECTION", outputs)

    def test_default_resume_behavior_continues(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        inputs = iter([url, "7", ""])
        outputs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            source_lookup = state_store.get_or_create_source(url, url)
            state_store.save_checkpoint(
                source_lookup.source.source_id,
                last_discovered_position=12,
                last_completed_position=10,
                last_exported_position=10,
            )

            exit_code = run_interactive(
                config=ScraperConfig(
                    max_records_per_run=10,
                    state_database_path=state_store.database_path,
                    preview_manifest_path=Path(temp_dir) / "source_manifest.json",
                    raw_html_dir=Path(temp_dir) / "raw_html",
                    request_delay_seconds=0,
                ),
                fetcher=FakeFetcher(),
                input_func=lambda _: next(inputs),
                output_func=outputs.append,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("RESUME MENU", outputs)
        self.assertIn("Resume action: continue", outputs)

    def test_no_accidental_overwrite_on_blank_resume_choice(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        inputs = iter([url, "7", ""])
        outputs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            source_lookup = state_store.get_or_create_source(url, url)
            state_store.save_checkpoint(source_lookup.source.source_id, last_exported_position=5)

            run_interactive(
                config=ScraperConfig(
                    max_records_per_run=10,
                    state_database_path=state_store.database_path,
                    preview_manifest_path=Path(temp_dir) / "source_manifest.json",
                    raw_html_dir=Path(temp_dir) / "raw_html",
                    request_delay_seconds=0,
                ),
                fetcher=FakeFetcher(),
                input_func=lambda _: next(inputs),
                output_func=outputs.append,
            )

        self.assertNotIn("Resume action: overwrite_previous_exports", outputs)
        self.assertIn("Resume action: continue", outputs)

    def test_overwrite_prompt_targets_one_batch_folder(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        inputs = iter(["2", "yes"])
        outputs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            (export_root / "burning_man" / "2022" / "batches" / "batch_001").mkdir(parents=True)
            (export_root / "burning_man" / "2022" / "batches" / "batch_002").mkdir(parents=True)
            state_store = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            source_lookup = state_store.get_or_create_source(url, url)

            selected = prompt_for_overwrite_batch(
                source_lookup=source_lookup,
                export_root=export_root,
                input_func=lambda _: next(inputs),
                output_func=outputs.append,
            )

        self.assertEqual(selected, 2)
        self.assertTrue(any("batch_002" in output for output in outputs))
        self.assertTrue(any("This will replace files only in:" in output for output in outputs))

    def test_overwrite_prompt_requires_confirmation(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        inputs = iter(["1", "no"])

        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            (export_root / "burning_man" / "2022" / "batches" / "batch_001").mkdir(parents=True)
            state_store = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            source_lookup = state_store.get_or_create_source(url, url)

            selected = prompt_for_overwrite_batch(
                source_lookup=source_lookup,
                export_root=export_root,
                input_func=lambda _: next(inputs),
                output_func=lambda _message: None,
            )

        self.assertIsNone(selected)

    def test_only_one_valid_installation_detail_is_parsed(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        inputs = iter([url, "2", "2"])
        outputs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            fetcher = ArchiveFakeFetcher()
            run_interactive(
                config=ScraperConfig(
                    max_records_per_run=10,
                    state_database_path=Path(temp_dir) / "scraper_state.sqlite3",
                    preview_manifest_path=Path(temp_dir) / "source_manifest.json",
                    raw_html_dir=Path(temp_dir) / "raw_html",
                    request_delay_seconds=0,
                ),
                fetcher=fetcher,
                input_func=lambda _: next(inputs),
                output_func=outputs.append,
            )

        self.assertEqual(
            fetcher.requested_urls,
            [
                "https://history.burningman.org/robots.txt",
                "https://history.burningman.org/art-history/archive/?yyyy=2022",
                "https://history.burningman.org/art-history/installation/malformed-record/",
                "https://history.burningman.org/art-history/installation/temple-of-dust/",
            ],
        )
        self.assertTrue(any("Candidate installation links: 2" in output for output in outputs))
        self.assertIn("ARTELIER IMPORT PREVIEW", outputs)
        self.assertIn("CSV HEADER", outputs)
        self.assertIn("CSV FIRST ROW", outputs)
        self.assertIn("UNMAPPED SOURCE FIELDS", outputs)
        self.assertTrue(any("PREVIEW COMPLETE" in output for output in outputs))

    def test_preview_attempts_no_more_than_five_candidates(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
        candidate_urls = [
            f"https://history.burningman.org/art-history/installation/malformed-{number}/"
            for number in range(1, 7)
        ]

        class AlwaysMalformedFetcher:
            def __init__(self):
                self.requested_urls: list[str] = []

            def fetch(self, candidate_url: str, allowed_urls: set[str]):
                if candidate_url not in allowed_urls:
                    raise ValueError("outside boundary")
                self.requested_urls.append(candidate_url)
                return FetchResult(
                    requested_url=candidate_url,
                    final_url=candidate_url,
                    status_code=200,
                    fetched_timestamp="2026-06-17T00:00:00+00:00",
                    content_type="text/html",
                    response_hash="malformed",
                    etag=None,
                    last_modified=None,
                    body=(Path(__file__).resolve().parent / "fixtures" / "malformed_record.html").read_bytes(),
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            source_lookup = state_store.get_or_create_source(url, url)
            fetcher = AlwaysMalformedFetcher()
            inspection = PageInspection(
                entered_url=url,
                normalized_url=url,
                final_url=url,
                canonical_url=url,
                page_title="Archive",
                detected_year="2022",
                detected_page_type="filtered archive listing page",
                robots_txt_status="200 https://history.burningman.org/robots.txt",
                candidate_installation_links=candidate_urls,
                pagination_detected=False,
                candidate_internal_links=candidate_urls,
                excluded_links=[],
            )

            parse_preview = build_first_record_preview(
                inspection=inspection,
                source_lookup=source_lookup,
                source_result=FetchResult(
                    requested_url=url,
                    final_url=url,
                    status_code=200,
                    fetched_timestamp="2026-06-17T00:00:00+00:00",
                    content_type="text/html",
                    response_hash="archive",
                    etag=None,
                    last_modified=None,
                    body=b"",
                ),
                fetcher=fetcher,  # type: ignore[arg-type]
                state_store=state_store,
                preview_run_id="preview-run",
                output_func=lambda _message: None,
            )

        self.assertIsNone(parse_preview)
        self.assertEqual(len(fetcher.requested_urls), 5)


if __name__ == "__main__":
    unittest.main()
