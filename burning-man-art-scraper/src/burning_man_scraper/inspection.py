from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urldefrag, urljoin, urlsplit

from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.inline_archive import extract_inline_archive_records, is_donate_link_text
from burning_man_scraper.state import detect_year
from burning_man_scraper.url_utils import ALLOWED_HOSTNAME, normalize_url


PARSER_VERSION = "phase3-html-inspector-v2-inline-archive"


@dataclass(frozen=True)
class LinkDecision:
    url: str
    reason: str


@dataclass
class ParsedLink:
    href: str
    rel: str | None = None
    class_name: str | None = None
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in self.text_parts if part.strip())


@dataclass(frozen=True)
class PageInspection:
    entered_url: str
    normalized_url: str
    final_url: str
    canonical_url: str | None
    page_title: str | None
    detected_year: str | None
    detected_page_type: str
    robots_txt_status: str
    candidate_installation_links: list[str]
    pagination_detected: bool
    candidate_internal_links: list[str]
    excluded_links: list[LinkDecision]
    parser_version: str = PARSER_VERSION


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.canonical_url: str | None = None
        self.links: list[ParsedLink] = []
        self.anchor_stack: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value for key, value in attrs}
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "link" and attr_map.get("rel") == "canonical":
            self.canonical_url = attr_map.get("href")
        if tag.lower() == "a" and attr_map.get("href"):
            self.links.append(
                ParsedLink(
                    href=attr_map["href"] or "",
                    rel=attr_map.get("rel"),
                    class_name=attr_map.get("class"),
                )
            )
            self.anchor_stack.append(len(self.links) - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() == "a" and self.anchor_stack:
            self.anchor_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.anchor_stack:
            self.links[self.anchor_stack[-1]].text_parts.append(data)

    @property
    def title(self) -> str | None:
        title = " ".join(part.strip() for part in self.title_parts if part.strip())
        return title or None


def inspect_html(
    entered_url: str,
    normalized_url: str,
    fetch_result: FetchResult,
    robots_result: FetchResult | None,
) -> PageInspection:
    parser = _LinkParser()
    parser.feed(fetch_result.text)

    final_url = fetch_result.final_url
    canonical_url = urljoin(final_url, parser.canonical_url) if parser.canonical_url else None
    detected_page_type = detect_page_type(normalized_url, canonical_url)
    candidate_links: list[str] = []
    internal_links: list[str] = []
    excluded_links: list[LinkDecision] = []
    pagination_detected = False
    inline_records = extract_inline_archive_records(fetch_result.text, normalized_url, final_url)
    for inline_record in inline_records:
        if inline_record.source_url not in candidate_links:
            candidate_links.append(inline_record.source_url)
        if inline_record.source_url not in internal_links:
            internal_links.append(inline_record.source_url)

    for link in parser.links:
        raw_href = link.href
        if is_donate_link_text(link.text, raw_href, link.class_name):
            excluded_links.append(LinkDecision(display_link_url(raw_href, final_url), "donate_link_ignored"))
            continue

        href_without_fragment, fragment = urldefrag(raw_href.strip())
        if fragment and not href_without_fragment:
            continue
        if not href_without_fragment:
            excluded_links.append(LinkDecision(raw_href, "empty_or_fragment_link"))
            continue
        if href_without_fragment.startswith(("mailto:", "tel:", "javascript:")):
            excluded_links.append(LinkDecision(raw_href, "non_http_link"))
            continue

        absolute_url = normalize_url(urljoin(final_url, href_without_fragment))
        if fragment and absolute_url == normalize_url(final_url):
            continue
        parsed = urlsplit(absolute_url)
        if parsed.hostname != ALLOWED_HOSTNAME:
            excluded_links.append(LinkDecision(absolute_url, "external_website"))
            continue

        if is_pagination_link(absolute_url, link.rel, link.class_name):
            pagination_detected = True
            excluded_links.append(LinkDecision(absolute_url, "pagination_not_authorized"))
            continue

        if is_installation_detail_url(absolute_url):
            if absolute_url not in candidate_links:
                candidate_links.append(absolute_url)
            if absolute_url not in internal_links:
                internal_links.append(absolute_url)
            continue

        if is_archive_or_discovery_url(absolute_url):
            excluded_links.append(LinkDecision(absolute_url, "archive_or_discovery_page_outside_boundary"))
        else:
            excluded_links.append(LinkDecision(absolute_url, "internal_non_installation_link"))

        if absolute_url not in internal_links:
            internal_links.append(absolute_url)

    return PageInspection(
        entered_url=entered_url,
        normalized_url=normalized_url,
        final_url=final_url,
        canonical_url=canonical_url,
        page_title=parser.title,
        detected_year=detect_year(normalized_url) or detect_year(final_url),
        detected_page_type=detected_page_type,
        robots_txt_status=robots_status(robots_result),
        candidate_installation_links=candidate_links,
        pagination_detected=pagination_detected,
        candidate_internal_links=internal_links,
        excluded_links=excluded_links,
    )


def detect_page_type(normalized_url: str, canonical_url: str | None = None) -> str:
    parsed = urlsplit(canonical_url or normalized_url)
    query = parse_qs(parsed.query)
    path = parsed.path.rstrip("/") + "/"

    if is_installation_detail_url(canonical_url or normalized_url):
        return "single installation detail page"
    if path == "/art-history/archive/":
        if query:
            return "filtered archive listing page"
        return "archive listing page"
    return "unsupported page"


def is_installation_detail_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    detail_markers = (
        "/art-history/installation/",
        "/art-history/installations/",
        "/art-history/artwork/",
        "/art-history/artworks/",
    )
    return any(marker in path for marker in detail_markers)


def is_archive_or_discovery_url(url: str) -> bool:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") + "/"
    if path in {"/", "/art-history/", "/art-history/archive/"}:
        return True
    return "/art-history/archive/" in path


def is_pagination_link(url: str, rel: str | None, class_name: str | None) -> bool:
    query = parse_qs(urlsplit(url).query)
    if "page" in query or "paged" in query:
        return True
    combined = " ".join(value for value in (rel, class_name) if value).lower()
    return "next" in combined or "prev" in combined or "pagination" in combined


def display_link_url(raw_href: str, final_url: str) -> str:
    href_without_fragment, _fragment = urldefrag(raw_href.strip())
    if not href_without_fragment:
        return raw_href
    absolute_url = urljoin(final_url, href_without_fragment)
    parsed = urlsplit(absolute_url)
    if parsed.scheme in {"http", "https"}:
        return normalize_url(absolute_url)
    return raw_href


def robots_status(robots_result: FetchResult | None) -> str:
    if robots_result is None:
        return "not fetched"
    return f"{robots_result.status_code} {robots_result.final_url}"
