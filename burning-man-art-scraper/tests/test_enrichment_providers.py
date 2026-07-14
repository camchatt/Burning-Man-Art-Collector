from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.enrichment.cache import SearchCache
from burning_man_scraper.enrichment.models import BatchRecord, SearchResult
from burning_man_scraper.enrichment.discovery import (
    FirstPartyDiscoveryConfig,
    discover_first_party_results,
    parse_sitemap,
    rank_candidate_urls,
)
from burning_man_scraper.enrichment.providers import (
    BraveSearchProvider,
    DuckDuckGoSearchProvider,
    NoOpSearchProvider,
    SearXNGSearchProvider,
    select_search_provider,
)


class FakeResponse:
    def __init__(self, payload: str):
        self.payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.payload


class CountingProvider:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def search(self, query: str, limit: int = 10):
        self.calls += 1
        return [SearchResult("Result", "https://example.com/result", "snippet", provider=self.name)]


class FakeFetch:
    def __init__(self, pages):
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise KeyError(url)
        return self.pages[url]


def record() -> BatchRecord:
    return BatchRecord(
        batch_index=1,
        project_record_id="temple",
        project_title="Temple of Dust",
        contributor_name="Avery Stone",
        year="2022",
        artist_website="https://artist.example/temple",
    )


class EnrichmentProviderTests(unittest.TestCase):
    def test_provider_selection_none(self):
        provider, log = select_search_provider("none", searxng_base_url="", brave_api_key="")

        self.assertIsInstance(provider, NoOpSearchProvider)
        self.assertEqual(log.selected_provider, "none")

    def test_provider_selection_brave_requires_key(self):
        with self.assertRaisesRegex(ValueError, "BRAVE_SEARCH_API_KEY"):
            select_search_provider("brave", brave_api_key="")

    def test_provider_selection_duckduckgo(self):
        with patch.object(DuckDuckGoSearchProvider, "available", return_value=True):
            provider, log = select_search_provider("duckduckgo", searxng_base_url="", brave_api_key="")

        self.assertIsInstance(provider, DuckDuckGoSearchProvider)
        self.assertEqual(log.selected_provider, "duckduckgo")

    def test_auto_prefers_duckduckgo_before_brave(self):
        with patch.object(DuckDuckGoSearchProvider, "available", return_value=True):
            provider, log = select_search_provider("auto", searxng_base_url="", brave_api_key="brave-key")

        self.assertIsInstance(provider, DuckDuckGoSearchProvider)
        self.assertEqual(log.selected_provider, "duckduckgo")

    def test_provider_selection_no_brave_key_auto_falls_back_to_none(self):
        with patch.object(DuckDuckGoSearchProvider, "available", return_value=False):
            provider, _log = select_search_provider("auto", searxng_base_url="", brave_api_key="")

        self.assertIsInstance(provider, NoOpSearchProvider)

    def test_auto_mode_with_local_searxng_available(self):
        with patch.object(SearXNGSearchProvider, "health_check", return_value=True):
            provider, log = select_search_provider("auto", searxng_base_url="http://localhost:8080", brave_api_key="")

        self.assertIsInstance(provider, SearXNGSearchProvider)
        self.assertEqual(log.selected_provider, "searxng")

    def test_auto_mode_with_searxng_unavailable(self):
        with patch.object(SearXNGSearchProvider, "health_check", return_value=False):
            with patch.object(DuckDuckGoSearchProvider, "available", return_value=False):
                provider, log = select_search_provider("auto", searxng_base_url="http://localhost:8080", brave_api_key="")

        self.assertIsInstance(provider, NoOpSearchProvider)
        self.assertTrue(log.failures)

    def test_searxng_json_normalization(self):
        payload = json.dumps(
            {
                "results": [
                    {
                        "title": "Temple",
                        "url": "https://artist.example/temple",
                        "content": "Temple of Dust",
                        "engine": "mock",
                    }
                ]
            }
        )
        with patch("burning_man_scraper.enrichment.providers.urlopen", return_value=FakeResponse(payload)):
            provider = SearXNGSearchProvider("http://localhost:8080", min_delay_seconds=0, max_retries=0)
            results = provider.search("Temple", limit=5)

        self.assertEqual(results[0].provider, "searxng")
        self.assertEqual(results[0].engine_metadata["engine"], "mock")

    def test_searxng_timeout_and_malformed_response(self):
        with patch("burning_man_scraper.enrichment.providers.urlopen", return_value=FakeResponse("[]")):
            provider = SearXNGSearchProvider("http://localhost:8080", min_delay_seconds=0, max_retries=0)
            with self.assertRaisesRegex(RuntimeError, "SearXNG search failed"):
                provider.search("Temple")

    def test_duckduckgo_adapter_with_mocked_responses(self):
        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def text(self, query, max_results=10):
                return [{"title": "Temple", "href": "https://example.com", "body": "Avery Stone"}]

        with patch.dict("sys.modules", {"duckduckgo_search": type("M", (), {"DDGS": FakeDDGS})}):
            provider = DuckDuckGoSearchProvider(min_delay_seconds=0)
            results = provider.search("Temple")

        self.assertEqual(results[0].provider, "duckduckgo")

    def test_search_cache_hit_miss_and_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SearchCache(Path(temp_dir))
            provider = CountingProvider()

            first = cache.search(provider, "Temple", limit=1)
            second = cache.search(provider, "Temple", limit=1)
            third = cache.search(provider, "Temple", limit=1, refresh=True)

        self.assertEqual(provider.calls, 2)
        self.assertEqual(first[0].url, second[0].url)
        self.assertEqual(third[0].url, first[0].url)

    def test_sitemap_discovery_and_limits(self):
        xml = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>https://artist.example/temple-of-dust</loc></sitemap>
        <sitemap><loc>https://artist.example/other</loc></sitemap>
        </sitemapindex>"""
        urls = parse_sitemap(xml, FirstPartyDiscoveryConfig(max_sitemap_urls_per_domain=1))

        self.assertEqual(urls, ["https://artist.example/temple-of-dust"])

    def test_rss_discovery_and_first_party_url_ranking(self):
        pages = {
            "https://artist.example/temple": (
                "<html><head><link rel='alternate' type='application/rss+xml' href='/feed.xml'></head>"
                "<body><a href='/temple-of-dust-burning-man-2022'>Temple</a></body></html>"
            ),
            "https://artist.example/robots.txt": "Sitemap: https://artist.example/sitemap.xml",
            "https://artist.example/sitemap.xml": (
                "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                "<url><loc>https://artist.example/temple-of-dust-materials</loc></url>"
                "</urlset>"
            ),
        }

        results, report = discover_first_party_results(record(), FakeFetch(pages))
        ranked = rank_candidate_urls(record(), ["https://artist.example/other", "https://artist.example/temple-of-dust"])

        self.assertTrue(any(result.provider == "first_party_discovery" for result in results))
        self.assertTrue(report.rss_feeds_inspected)
        self.assertEqual(ranked[0], "https://artist.example/temple-of-dust")


if __name__ == "__main__":
    unittest.main()
