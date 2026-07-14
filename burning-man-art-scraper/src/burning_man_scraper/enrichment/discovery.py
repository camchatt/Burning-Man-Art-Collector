from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET
from urllib.parse import urldefrag, urljoin, urlsplit

from burning_man_scraper.enrichment.models import BatchRecord, SearchResult


@dataclass
class FirstPartyDiscoveryConfig:
    max_sitemaps_per_domain: int = 10
    max_sitemap_urls_per_domain: int = 5000
    max_internal_links_per_seed: int = 200
    max_first_party_pages_fetched_per_record: int = 20


@dataclass
class FirstPartyDiscoveryReport:
    known_urls_inspected: list[str] = field(default_factory=list)
    sitemaps_inspected: list[str] = field(default_factory=list)
    rss_feeds_inspected: list[str] = field(default_factory=list)
    first_party_candidate_urls_discovered: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    source_fragments: dict[str, str] = field(default_factory=dict)
    base_sources_skipped: list[str] = field(default_factory=list)


def discover_first_party_results(
    record: BatchRecord,
    fetch_client,
    config: FirstPartyDiscoveryConfig | None = None,
) -> tuple[list[SearchResult], FirstPartyDiscoveryReport]:
    config = config or FirstPartyDiscoveryConfig()
    report = FirstPartyDiscoveryReport()
    base_url, fragment = base_source_metadata(record)
    if base_url and is_base_burning_man_source(base_url):
        report.base_sources_skipped.append(base_url)
        if fragment:
            report.source_fragments[base_url] = fragment
    seed_urls = known_urls(record)
    results: list[SearchResult] = [
        SearchResult(title=urlsplit(url).netloc, url=url, snippet="Known URL from scrape batch.", provider="known_url")
        for url in seed_urls
    ]
    seen = set(seed_urls)
    fetched_pages = 0

    for seed in seed_urls:
        if fetched_pages >= config.max_first_party_pages_fetched_per_record:
            break
        report.known_urls_inspected.append(seed)
        try:
            html = fetch_client.fetch(seed)
            fetched_pages += 1
        except Exception as exc:
            report.failures.append(f"{seed}: {exc}")
            continue

        same_domain = same_domain_urls(seed, extract_links(html), config.max_internal_links_per_seed)
        feed_urls = same_domain_urls(seed, extract_feed_urls(html, seed) + common_feed_urls(seed), 20)
        report.rss_feeds_inspected.extend(feed_urls)
        sitemap_urls = sitemap_locations(seed, fetch_client, report, config)
        candidates = same_domain + feed_urls + sitemap_urls
        ranked = rank_candidate_urls(record, candidates)
        for url in ranked:
            if url in seen:
                continue
            seen.add(url)
            report.first_party_candidate_urls_discovered.append(url)
            results.append(SearchResult(title=urlsplit(url).path or url, url=url, snippet="First-party discovery.", provider="first_party_discovery"))
            if len(report.first_party_candidate_urls_discovered) >= config.max_first_party_pages_fetched_per_record:
                break
    return results, report


def known_urls(record: BatchRecord) -> list[str]:
    values = [record.artist_website, record.project_website, record.source_url]
    result: list[str] = []
    for value in values:
        if not value or not value.startswith(("http://", "https://")):
            continue
        fetch_url, _fragment = normalize_fetch_url(value)
        if is_base_burning_man_source(fetch_url):
            continue
        if fetch_url not in result:
            result.append(fetch_url)
    return result


def base_source_metadata(record: BatchRecord) -> tuple[str | None, str | None]:
    if not record.source_url:
        return None, None
    return normalize_fetch_url(record.source_url)


def sitemap_locations(seed_url: str, fetch_client, report: FirstPartyDiscoveryReport, config: FirstPartyDiscoveryConfig) -> list[str]:
    parsed = urlsplit(seed_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_urls = [f"{base}/sitemap.xml"]
    try:
        robots = fetch_client.fetch(f"{base}/robots.txt")
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_urls.append(line.split(":", 1)[1].strip())
    except Exception as exc:
        report.failures.append(f"{base}/robots.txt: {exc}")

    page_urls: list[str] = []
    for sitemap_url in unique(sitemap_urls)[: config.max_sitemaps_per_domain]:
        try:
            xml = fetch_client.fetch(sitemap_url)
            report.sitemaps_inspected.append(sitemap_url)
            page_urls.extend(parse_sitemap(xml, config))
        except Exception as exc:
            report.failures.append(f"{sitemap_url}: {exc}")
    return page_urls[: config.max_sitemap_urls_per_domain]


def parse_sitemap(xml: str, config: FirstPartyDiscoveryConfig) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return urls
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    for loc in root.findall(".//sm:loc", namespace) + root.findall(".//loc"):
        if loc.text:
            urls.append(loc.text.strip())
        if len(urls) >= config.max_sitemap_urls_per_domain:
            break
    return urls


def rank_candidate_urls(record: BatchRecord, urls: list[str]) -> list[str]:
    scored = []
    for url in unique(urls):
        text = url.lower().replace("-", " ").replace("_", " ")
        score = 0
        for token in [record.project_title, record.contributor_name, record.year, "burning man", "installation", "sculpture", "fabrication", "materials"]:
            if token and matching_identifiers_text(text, token):
                score += 5
        scored.append((score, url))
    return [url for score, url in sorted(scored, key=lambda item: (-item[0], item[1])) if score > 0]


def matching_identifiers_text(haystack: str, needle: str) -> bool:
    return clean_text(needle).lower().replace("-", " ") in haystack


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def extract_links(html: str) -> list[str]:
    parser = _LinkParser()
    parser.feed(html)
    return parser.links


def extract_feed_urls(html: str, base_url: str) -> list[str]:
    parser = _LinkParser()
    parser.feed(html)
    return [urljoin(base_url, href) for href in parser.feed_links]


def common_feed_urls(seed_url: str) -> list[str]:
    parsed = urlsplit(seed_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [f"{base}/feed", f"{base}/rss.xml", f"{base}/atom.xml"]


def same_domain_urls(seed_url: str, urls: list[str], limit: int) -> list[str]:
    seed_host = urlsplit(seed_url).hostname
    result: list[str] = []
    for url in urls:
        absolute = urljoin(seed_url, url)
        if urlsplit(absolute).hostname == seed_host and absolute not in result:
            result.append(absolute)
        if len(result) >= limit:
            break
    return result


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def normalize_fetch_url(url: str) -> tuple[str, str | None]:
    base_url, fragment = urldefrag(url)
    return base_url, fragment or None


def is_base_burning_man_source(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "history.burningman.org"


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.feed_links: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attr_map = {key.lower(): value for key, value in attrs}
        if tag.lower() == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"] or "")
        if tag.lower() == "link" and attr_map.get("href"):
            rel = (attr_map.get("rel") or "").lower()
            type_value = (attr_map.get("type") or "").lower()
            if "alternate" in rel and ("rss" in type_value or "atom" in type_value):
                self.feed_links.append(attr_map["href"] or "")
