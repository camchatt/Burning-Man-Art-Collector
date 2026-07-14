from pathlib import Path
import csv
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.enrichment.models import BatchRecord, SearchResult
from burning_man_scraper.enrichment.research import (
    CachedFetchClient,
    build_enrichment_preview,
    build_search_queries,
    classify_source_type,
    matching_identifiers,
    normalize_fetch_url,
    rank_and_fetch_sources,
    robots_allowed,
    write_preview_files,
)
from burning_man_scraper.enrichment.providers import NoOpSearchProvider


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")


class MockSearch:
    def __init__(self, results):
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 10):
        self.queries.append(query)
        return self.results


class MockFetch:
    def __init__(self, pages):
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url: str):
        self.fetched.append(url)
        return self.pages[url]


class FailingSearch:
    name = "failing"

    def search(self, query: str, limit: int = 10):
        raise RuntimeError("provider unavailable")


class StaticSearch:
    name = "duckduckgo"

    def __init__(self, results):
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 10):
        self.queries.append(query)
        return self.results[:limit]


def sample_record() -> BatchRecord:
    return BatchRecord(
        batch_index=1,
        project_record_id="temple-of-dust",
        project_title="Temple of Dust",
        contributor_name="Avery Stone",
        year="2022",
        source_url="https://history.burningman.org/art-history/archive/?yyyy=2022#temple",
        artist_website="https://averystone.studio/temple",
        materials=None,
        original_values={"title": "Temple of Dust", "artist_display_text": "Avery Stone"},
        artelier_row={
            header: ""
            for header in SCHEMA.headers
        }
        | {
            "project_title": "Temple of Dust",
            "project_slug": "temple-of-dust",
            "project_year": "2022",
            "contributor_name": "Avery Stone",
            "proof_external_url": "https://history.burningman.org/art-history/archive/?yyyy=2022#temple",
        },
    )


class EnrichmentResearchTests(unittest.TestCase):
    def test_search_query_generation(self):
        queries = build_search_queries(sample_record())

        self.assertIn('"Temple of Dust" "Avery Stone"', queries)
        self.assertIn('"Temple of Dust" Burning Man', queries)
        self.assertIn('"Temple of Dust" fabrication', queries)
        self.assertIn('"Avery Stone" Burning Man 2022', queries)

    def test_search_queries_from_title_contributor_alias_and_year(self):
        record = BatchRecord(
            batch_index=1,
            project_record_id="watching",
            project_title="... the world is Watching.",
            contributor_name="Eric Tussey a.k.a. pebble",
            year="2022",
            artist_website="https://tussey.com/world-is-watching",
            original_values={"artist_location": "Boulder, CO"},
        )

        queries = build_search_queries(record)

        self.assertIn('" ... the world is Watching."'.replace('" ', '"'), queries[0])
        self.assertIn('"... the world is Watching." "Eric Tussey"', queries)
        self.assertIn('"... the world is Watching." Burning Man 2022', queries)
        self.assertIn('"Eric Tussey" pebble Burning Man', queries)
        self.assertIn('"Eric Tussey" kinetic installation', queries)
        self.assertIn('"Eric Tussey" artist Boulder', queries)
        self.assertIn('site:tussey.com "... the world is Watching."', queries)

    def test_source_ranking(self):
        record = sample_record()
        self.assertEqual(classify_source_type("https://averystone.studio/temple", record), "first_party")
        self.assertEqual(classify_source_type("https://history.burningman.org/art/temple", record), "burning_man_official")
        self.assertEqual(classify_source_type("https://publicartarchive.org/art/temple", record), "public_art_archive")

    def test_project_identity_matching(self):
        matches = matching_identifiers(
            sample_record(),
            "Temple of Dust",
            "",
            "Avery Stone created Temple of Dust for Burning Man 2022.",
        )

        self.assertIn("project title", matches)
        self.assertIn("contributor name", matches)
        self.assertIn("event year", matches)
        self.assertIn("Burning Man reference", matches)

    def test_rejection_of_weak_matches(self):
        record = sample_record()
        results = [SearchResult("Dust report", "https://example.com/dust", "Dust storms in Nevada")]
        fetch = MockFetch({"https://example.com/dust": "<html><title>Dust</title><p>No matching project.</p></html>"})

        sources = rank_and_fetch_sources(record, results, fetch)

        self.assertEqual(sources, [])

    def test_field_level_evidence_storage_and_classification(self):
        record = sample_record()
        result = SearchResult(
            "Temple of Dust - Avery Stone Studio",
            "https://averystone.studio/temple",
            "Temple of Dust Burning Man 2022",
        )
        page = """
        <html><title>Temple of Dust</title>
        <p>Avery Stone built Temple of Dust for Burning Man 2022.</p>
        <p>Materials: wood, steel, and dust.</p>
        <p>The interactive memorial invited community participation.</p>
        </html>
        """

        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=MockSearch([result]),
            fetch_client=MockFetch({result.url: page}),
            preview_id="preview-test",
        )

        by_field = {change.artelier_field: change for change in preview.proposed_changes}
        self.assertEqual(by_field["contributor_website"].evidence_classification, "directly_stated")
        self.assertEqual(by_field["project_materials"].proposed_value, "wood, steel, and dust")
        self.assertEqual(by_field["project_tags"].evidence_classification, "strongly_inferred")

    def test_blank_field_preservation(self):
        record = sample_record()
        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=MockSearch([]),
            fetch_client=MockFetch({}),
            preview_id="preview-test",
        )

        self.assertEqual(preview.artelier_row["project_materials"], "")
        self.assertEqual(preview.artelier_row["project_fabrication_methods"], "")

    def test_no_image_only_material_inference(self):
        record = sample_record()
        result = SearchResult(
            "Temple of Dust photo",
            "https://photos.example/temple",
            "Temple of Dust Avery Stone Burning Man 2022",
        )
        page = "<html><title>Temple of Dust</title><img src='wood.jpg' alt='wood and steel'></html>"

        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=MockSearch([result]),
            fetch_client=MockFetch({result.url: page}),
            preview_id="preview-test",
        )

        self.assertNotIn("project_materials", {change.artelier_field for change in preview.proposed_changes})

    def test_exact_artelier_header_preview_and_preview_files(self):
        record = sample_record()
        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=MockSearch([]),
            fetch_client=MockFetch({}),
            preview_id="preview-test",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_preview_files(preview, Path(temp_dir))
            with paths[2].open("r", encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            payload = json.loads(paths[0].read_text(encoding="utf-8"))

        self.assertEqual(headers, SCHEMA.headers)
        self.assertEqual(payload["preview_id"], "preview-test")

    def test_one_record_only_preview_behavior(self):
        record = sample_record()
        search = MockSearch([])

        build_enrichment_preview(record, SCHEMA, search_client=search, fetch_client=MockFetch({}))

        self.assertGreater(len(search.queries), 0)
        self.assertTrue(all("Temple of Dust" in query or "Avery Stone" in query for query in search.queries))

    def test_preview_completion_using_known_urls_only(self):
        record = sample_record()
        page = """
        <html><title>Temple of Dust</title>
        <p>Avery Stone built Temple of Dust for Burning Man 2022.</p>
        <p>Materials: reclaimed wood.</p>
        </html>
        """

        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=NoOpSearchProvider(),
            fetch_client=MockFetch({"https://averystone.studio/temple": page}),
            preview_id="known-only",
        )

        self.assertTrue(preview.sources)
        self.assertTrue(preview.search_report["completed_without_broad_web_search"])

    def test_preview_completion_when_general_search_provider_fails(self):
        record = sample_record()

        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=FailingSearch(),
            fetch_client=MockFetch({}),
            preview_id="provider-fails",
        )

        self.assertEqual(preview.preview_id, "provider-fails")
        self.assertTrue(preview.search_report["provider_failures"])

    def test_url_fragments_removed_before_http_and_fragments_stored(self):
        url = "https://history.burningman.org/art-history/archive/?yyyy=2022#a2I8X00000h8YmQUAU"

        fetch_url, fragment = normalize_fetch_url(url)

        self.assertEqual(fetch_url, "https://history.burningman.org/art-history/archive/?yyyy=2022")
        self.assertEqual(fragment, "a2I8X00000h8YmQUAU")

    def test_blocked_base_source_does_not_stop_external_search(self):
        record = BatchRecord(
            batch_index=1,
            project_record_id="watching",
            project_title="... the world is Watching.",
            contributor_name="Eric Tussey a.k.a. pebble",
            year="2022",
            source_url="https://history.burningman.org/art-history/archive/?yyyy=2022#a2I8X00000h8YmQUAU",
            artelier_row={header: "" for header in SCHEMA.headers}
            | {
                "project_title": "... the world is Watching.",
                "project_slug": "the-world-is-watching",
                "proof_external_url": "https://history.burningman.org/art-history/archive/?yyyy=2022#a2I8X00000h8YmQUAU",
            },
        )
        external = SearchResult(
            "Eric Tussey - The World is Watching",
            "https://tussey.com/world-is-watching",
            "Eric Tussey Burning Man 2022",
            provider="duckduckgo",
        )
        page = "<html><p>Eric Tussey created ... the world is Watching. for Burning Man 2022.</p></html>"

        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=StaticSearch([external]),
            fetch_client=MockFetch({"https://tussey.com/world-is-watching": page}),
            preview_id="external-continues",
        )

        self.assertEqual(preview.search_report["source_fragments"]["https://history.burningman.org/art-history/archive/?yyyy=2022"], "a2I8X00000h8YmQUAU")
        self.assertIn("https://history.burningman.org/art-history/archive/?yyyy=2022", preview.search_report["base_sources_skipped"])
        self.assertTrue(preview.sources)
        self.assertGreater(len(preview.search_report["queries_attempted"]), 0)

    def test_source_report_lists_attempted_queries_and_rejections(self):
        record = sample_record()
        weak = SearchResult("Weak", "https://weak.example/page", "unrelated", provider="duckduckgo")

        preview = build_enrichment_preview(
            record,
            SCHEMA,
            search_client=StaticSearch([weak]),
            fetch_client=MockFetch({"https://weak.example/page": "<html><p>Unrelated page.</p></html>"}),
            preview_id="report",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_preview_files(preview, Path(temp_dir))
            report = paths[3].read_text(encoding="utf-8")

        self.assertIn("Queries attempted:", report)
        self.assertIn("Rejected sources:", report)

    def test_robots_checking_uses_normalized_url(self):
        checked: list[str] = []

        class FakeRobot:
            def set_url(self, url):
                pass

            def read(self):
                pass

            def can_fetch(self, user_agent, url):
                checked.append(url)
                return True

        from unittest.mock import patch

        with patch("burning_man_scraper.enrichment.research.urllib.robotparser.RobotFileParser", return_value=FakeRobot()):
            robots_allowed("https://history.burningman.org/art-history/archive/?yyyy=2022#frag", "agent")

        self.assertEqual(checked, ["https://history.burningman.org/art-history/archive/?yyyy=2022"])


if __name__ == "__main__":
    unittest.main()
