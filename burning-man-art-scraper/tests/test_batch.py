from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.batch import process_approved_batch
from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.inspection import PageInspection
from burning_man_scraper.state import ScraperState


ARCHIVE_URL = "https://history.burningman.org/art-history/archive/?yyyy=2022"


def archive_html(titles: list[str]) -> bytes:
    return archive_html_pairs([(f"record-{index}", title) for index, title in enumerate(titles, start=1)])


def archive_html_pairs(records: list[tuple[str, str]]) -> bytes:
    articles = []
    for index, (fragment, title) in enumerate(records, start=1):
        articles.append(
            f"""
            <article>
              <h2><a href="#{fragment}">{title}</a></h2>
              <p>by: Artist {index}</p>
              <p>from: City {index}, NV</p>
              <p>year: 2022</p>
              <p>Description for {title}.</p>
              <img src="/images/{index}.jpg" alt="{title}">
              <p>Contact: <a href="mailto:artist{index}@example.com">artist{index}@example.com</a></p>
            </article>
            """
        )
    return ("<html><body>" + "\n".join(articles) + "</body></html>").encode("utf-8")


def source_result(titles: list[str]) -> FetchResult:
    return FetchResult(
        requested_url=ARCHIVE_URL,
        final_url=ARCHIVE_URL,
        status_code=200,
        fetched_timestamp="2026-06-17T00:00:00+00:00",
        content_type="text/html",
        response_hash="archive-hash",
        etag=None,
        last_modified=None,
        body=archive_html(titles),
    )


def source_result_pairs(records: list[tuple[str, str]]) -> FetchResult:
    return FetchResult(
        requested_url=ARCHIVE_URL,
        final_url=ARCHIVE_URL,
        status_code=200,
        fetched_timestamp="2026-06-17T00:00:00+00:00",
        content_type="text/html",
        response_hash="archive-hash",
        etag=None,
        last_modified=None,
        body=archive_html_pairs(records),
    )


def inspection_for(candidates: list[str]) -> PageInspection:
    return PageInspection(
        entered_url=ARCHIVE_URL,
        normalized_url=ARCHIVE_URL,
        final_url=ARCHIVE_URL,
        canonical_url=ARCHIVE_URL,
        page_title="Archive",
        detected_year="2022",
        detected_page_type="filtered archive listing page",
        robots_txt_status="200 https://history.burningman.org/robots.txt",
        candidate_installation_links=candidates,
        pagination_detected=False,
        candidate_internal_links=candidates,
        excluded_links=[],
    )


class EmptyFetcher:
    requested_urls: list[str]

    def __init__(self):
        self.requested_urls = []

    def fetch(self, url: str, allowed_urls: set[str]):
        self.requested_urls.append(url)
        raise AssertionError("Inline batch tests should not fetch detail pages.")


class DetailFetcher:
    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = bodies
        self.requested_urls: list[str] = []

    def fetch(self, url: str, allowed_urls: set[str]):
        self.requested_urls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            fetched_timestamp="2026-06-17T00:00:00+00:00",
            content_type="text/html",
            response_hash=url,
            etag=None,
            last_modified=None,
            body=self.bodies[url],
        )


class BatchTests(unittest.TestCase):
    def state_lookup(self):
        temp_dir = tempfile.TemporaryDirectory()
        state = ScraperState(Path(temp_dir.name) / "state.sqlite3")
        lookup = state.get_or_create_source(ARCHIVE_URL, ARCHIVE_URL)
        batch_id = state.save_preview_approval
        return temp_dir, state, lookup

    def create_batch(self, state: ScraperState, source_id: int) -> int:
        from burning_man_scraper.state import ApprovalContext

        context = ApprovalContext(
            preview_run_id="preview-run",
            source_id=source_id,
            normalized_source_url=ARCHIVE_URL,
            proposed_batch_number=state.next_proposed_batch_number(source_id),
            requested_count=10,
            preview_record_id="preview-record",
            schema_version="installation-preview-v1",
            parser_version="phase4-installation-preview-v1",
            configuration_hash="config",
            source_manifest_hash="manifest",
        )
        state.save_preview_approval(context, "approved")
        return state.create_pending_export_batch(context)

    def test_exact_requested_successful_count(self):
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            result = process_approved_batch(
                lookup,
                inspection_for([f"{ARCHIVE_URL}#record-{i}" for i in range(1, 4)]),
                source_result(["One", "Two", "Three"]),
                EmptyFetcher(),
                state,
                requested_count=2,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertEqual(result.succeeded, 2)
        self.assertEqual(result.attempted, 2)

    def test_next_unprocessed_selection_and_no_restart_from_record_1(self):
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            state.mark_source_record_by_canonical(
                lookup.source.source_id,
                1,
                f"{ARCHIVE_URL}#record-1",
                f"{ARCHIVE_URL}#record-1",
                "one",
                "completed",
            )
            result = process_approved_batch(
                lookup,
                inspection_for([f"{ARCHIVE_URL}#record-1", f"{ARCHIVE_URL}#record-2"]),
                source_result(["One", "Two"]),
                EmptyFetcher(),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(result.completed_urls, [f"{ARCHIVE_URL}#record-2"])

    def test_source_reorder_handling(self):
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            state.mark_source_record_by_canonical(
                lookup.source.source_id,
                2,
                f"{ARCHIVE_URL}#record-2",
                f"{ARCHIVE_URL}#record-2",
                "two",
                "completed",
            )
            result = process_approved_batch(
                lookup,
                inspection_for([f"{ARCHIVE_URL}#record-2", f"{ARCHIVE_URL}#record-1"]),
                source_result_pairs([("record-2", "Two"), ("record-1", "One")]),
                EmptyFetcher(),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertIn(f"{ARCHIVE_URL}#record-2", result.manifest_changes.reordered_links)
        self.assertEqual(result.completed_urls, [f"{ARCHIVE_URL}#record-1"])

    def test_new_link_insertion(self):
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            state.mark_source_record_by_canonical(
                lookup.source.source_id,
                2,
                f"{ARCHIVE_URL}#record-2",
                f"{ARCHIVE_URL}#record-2",
                "two",
                "completed",
            )
            result = process_approved_batch(
                lookup,
                inspection_for([f"{ARCHIVE_URL}#record-1", f"{ARCHIVE_URL}#record-2"]),
                source_result(["One", "Two"]),
                EmptyFetcher(),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertIn(f"{ARCHIVE_URL}#record-1", result.manifest_changes.new_links)
        self.assertEqual(result.completed_urls, [f"{ARCHIVE_URL}#record-1"])

    def test_removed_links(self):
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            state.mark_source_record_by_canonical(
                lookup.source.source_id,
                1,
                f"{ARCHIVE_URL}#record-removed",
                f"{ARCHIVE_URL}#record-removed",
                "removed",
                "completed",
            )
            result = process_approved_batch(
                lookup,
                inspection_for([f"{ARCHIVE_URL}#record-1"]),
                source_result(["One"]),
                EmptyFetcher(),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertIn(f"{ARCHIVE_URL}#record-removed", result.manifest_changes.removed_links)

    def test_individual_record_failure_does_not_stop_batch(self):
        bad = "https://history.burningman.org/art-history/installation/bad/"
        good = "https://history.burningman.org/art-history/installation/good/"
        bodies = {
            bad: b"<html><body><p>No title</p></body></html>",
            good: b"<html><body><h1>Good</h1><p class='artist'>Artist</p><p class='description'>Good.</p><img src='/good.jpg'></body></html>",
        }
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            result = process_approved_batch(
                lookup,
                inspection_for([bad, good]),
                source_result([]),
                DetailFetcher(bodies),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.succeeded, 1)

    def test_attempt_ceiling(self):
        candidates = [
            f"https://history.burningman.org/art-history/installation/bad-{i}/" for i in range(1, 6)
        ]
        bodies = {url: b"<html><body><p>No title</p></body></html>" for url in candidates}
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            result = process_approved_batch(
                lookup,
                inspection_for(candidates),
                source_result([]),
                DetailFetcher(bodies),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertEqual(result.attempt_ceiling, 2)
        self.assertEqual(result.attempted, 2)

    def test_idempotent_rerun_and_duplicate_skipping(self):
        temp_dir, state, lookup = self.state_lookup()
        with temp_dir:
            candidates = [f"{ARCHIVE_URL}#record-1"]
            first = process_approved_batch(
                lookup,
                inspection_for(candidates),
                source_result(["One"]),
                EmptyFetcher(),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )
            second = process_approved_batch(
                lookup,
                inspection_for(candidates),
                source_result(["One"]),
                EmptyFetcher(),
                state,
                requested_count=1,
                export_batch_id=self.create_batch(state, lookup.source.source_id),
                preview_run_id="preview-run",
            )

        self.assertEqual(first.succeeded, 1)
        self.assertEqual(second.succeeded, 0)
        self.assertEqual(second.duplicates, 1)


if __name__ == "__main__":
    unittest.main()
