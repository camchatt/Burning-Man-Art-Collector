from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import time
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from burning_man_scraper.enrichment.models import SearchResult


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        ...


@dataclass
class ProviderLog:
    selected_provider: str
    failures: list[str] = field(default_factory=list)


class NoOpSearchProvider:
    name = "none"

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return []


class BraveSearchProvider:
    name = "brave"

    def __init__(self, api_key: str | None = None, user_agent: str = "BurningManArtArchiveScraper/0.4 enrichment"):
        self.api_key = api_key or os.environ.get("BRAVE_SEARCH_API_KEY")
        self.user_agent = user_agent
        if not self.api_key:
            raise ValueError("BRAVE_SEARCH_API_KEY is required when ENRICHMENT_SEARCH_PROVIDER=brave.")

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": limit})
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key or "",
                "User-Agent": self.user_agent,
            },
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        results: list[SearchResult] = []
        for item in payload.get("web", {}).get("results", [])[:limit]:
            results.append(
                SearchResult(
                    title=clean_text(str(item.get("title") or "")),
                    url=str(item.get("url") or ""),
                    snippet=strip_html(str(item.get("description") or "")),
                    provider=self.name,
                    published_date=item.get("age"),
                    engine_metadata={"profile": item.get("profile")},
                )
            )
        return results


class SearXNGSearchProvider:
    name = "searxng"

    def __init__(
        self,
        base_url: str | None = None,
        user_agent: str = "BurningManArtArchiveScraper/0.4 enrichment",
        timeout_seconds: float = 10.0,
        min_delay_seconds: float = 2.0,
        max_retries: int = 2,
    ):
        self.base_url = (base_url or os.environ.get("SEARXNG_BASE_URL") or "").rstrip("/")
        if not self.base_url:
            raise ValueError("SEARXNG_BASE_URL is required when ENRICHMENT_SEARCH_PROVIDER=searxng.")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.min_delay_seconds = min_delay_seconds
        self.max_retries = max_retries
        self.last_request_at = 0.0

    def health_check(self) -> bool:
        try:
            self.search("health check", limit=1)
            return True
        except Exception:
            return False

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        params = urlencode({"q": query, "format": "json", "language": "en", "safesearch": "1"})
        url = f"{self.base_url}/search?{params}"
        payload = self._request_json(url)
        results: list[SearchResult] = []
        for item in payload.get("results", [])[:limit]:
            if not isinstance(item, dict):
                continue
            result_url = str(item.get("url") or "")
            if not result_url:
                continue
            results.append(
                SearchResult(
                    title=clean_text(str(item.get("title") or "")),
                    url=result_url,
                    snippet=strip_html(str(item.get("content") or item.get("snippet") or "")),
                    provider=self.name,
                    published_date=item.get("publishedDate") or item.get("published_date"),
                    engine_metadata={"engine": item.get("engine"), "category": item.get("category")},
                )
            )
        return results

    def _request_json(self, url: str) -> dict[str, object]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            elapsed = time.time() - self.last_request_at
            if elapsed < self.min_delay_seconds:
                time.sleep(self.min_delay_seconds - elapsed)
            try:
                request = Request(url, headers={"Accept": "application/json", "User-Agent": self.user_agent})
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    self.last_request_at = time.time()
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("SearXNG returned malformed JSON.")
                return payload
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"SearXNG search failed: {last_error}")


class DuckDuckGoSearchProvider:
    name = "duckduckgo"

    def __init__(self, min_delay_seconds: float = 3.0):
        self.min_delay_seconds = min_delay_seconds
        self.last_request_at = 0.0

    @staticmethod
    def available() -> bool:
        try:
            import duckduckgo_search  # noqa: F401
        except Exception:
            return False
        return True

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            from duckduckgo_search import DDGS
        except Exception as exc:
            raise RuntimeError("DuckDuckGo optional dependency is not installed.") from exc
        elapsed = time.time() - self.last_request_at
        if elapsed < self.min_delay_seconds:
            time.sleep(self.min_delay_seconds - elapsed)
        results: list[SearchResult] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=limit):
                results.append(
                    SearchResult(
                        title=clean_text(str(item.get("title") or "")),
                        url=str(item.get("href") or item.get("url") or ""),
                        snippet=strip_html(str(item.get("body") or "")),
                        provider=self.name,
                        engine_metadata={"source": "duckduckgo_search"},
                    )
                )
        self.last_request_at = time.time()
        return [result for result in results if result.url][:limit]


def select_search_provider(
    provider_name: str | None = None,
    searxng_base_url: str | None = None,
    brave_api_key: str | None = None,
    user_agent: str = "BurningManArtArchiveScraper/0.4 enrichment",
) -> tuple[SearchProvider, ProviderLog]:
    requested = (provider_name or os.environ.get("ENRICHMENT_SEARCH_PROVIDER") or "auto").strip().lower()
    searxng_url = searxng_base_url if searxng_base_url is not None else os.environ.get("SEARXNG_BASE_URL")
    brave_key = brave_api_key if brave_api_key is not None else os.environ.get("BRAVE_SEARCH_API_KEY")
    log = ProviderLog(selected_provider=requested)

    if requested == "none":
        return NoOpSearchProvider(), ProviderLog("none")
    if requested == "brave":
        return BraveSearchProvider(api_key=brave_key, user_agent=user_agent), ProviderLog("brave")
    if requested == "duckduckgo":
        if not DuckDuckGoSearchProvider.available():
            raise RuntimeError("DuckDuckGo provider selected, but duckduckgo-search is not installed.")
        return DuckDuckGoSearchProvider(), ProviderLog("duckduckgo")
    if requested == "searxng":
        provider = SearXNGSearchProvider(base_url=searxng_url, user_agent=user_agent)
        if not provider.health_check():
            log.failures.append("Configured SearXNG instance could not be reached.")
        return provider, ProviderLog("searxng", log.failures)
    if requested != "auto":
        raise ValueError("ENRICHMENT_SEARCH_PROVIDER must be auto, searxng, duckduckgo, brave, or none.")

    if searxng_url:
        try:
            provider = SearXNGSearchProvider(base_url=searxng_url, user_agent=user_agent)
            if provider.health_check():
                return provider, ProviderLog("searxng")
            log.failures.append("SearXNG health check failed; falling back.")
        except (URLError, RuntimeError, ValueError) as exc:
            log.failures.append(f"SearXNG unavailable: {exc}")
    if DuckDuckGoSearchProvider.available():
        return DuckDuckGoSearchProvider(), ProviderLog("duckduckgo", log.failures)
    if brave_key:
        return BraveSearchProvider(api_key=brave_key, user_agent=user_agent), ProviderLog("brave", log.failures)
    return NoOpSearchProvider(), ProviderLog("none", log.failures)


def strip_html(value: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", value))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()
