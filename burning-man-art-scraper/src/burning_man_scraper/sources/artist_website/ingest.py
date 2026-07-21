#!/usr/bin/env python3
"""Create auditable, review-first registry CSVs from an artist-owned website."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import unicodedata
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag


USER_AGENT = "ArtelierAggregator/0.1 (+local review-first artist website ingest)"
DEFAULT_TIMEOUT = 20
DEFAULT_DELAY = 1.0
DEFAULT_MAX_PAGES = 150
MAX_BODY_BYTES = 5_000_000

CSV_COLUMNS = [
    "project_title",
    "project_slug",
    "project_type",
    "project_year",
    "project_location",
    "project_summary",
    "client_name",
    "public_reference_url",
    "hero_image_url",
    "project_visibility",
    "contributor_name",
    "contributor_slug",
    "role_title",
    "contributor_email",
    "contributor_website",
    "contributor_visibility",
    "contribution_category",
    "contribution_title",
    "what_they_did",
    "why_it_mattered",
    "public_credit_language",
    "phase",
    "verification_status",
    "approval_status",
    "contribution_visibility",
    "proof_title",
    "proof_type",
    "proof_external_url",
    "proof_description",
    "proof_visibility",
    "permission_status",
]

INTERNAL_ARRAY_FIELDS = {
    "tags",
    "materials",
    "fabrication_methods",
    "context_tags",
    "image_urls",
}

PROJECT_TYPES = {
    "Public Art",
    "Sculpture",
    "Installation",
    "Interactive Installation",
    "Fabrication Project",
    "Architectural Feature",
    "Exhibition",
    "Museum / Cultural Project",
    "Experiential Environment",
    "Event / Temporary Activation",
    "Art Vehicle / Art Car",
    "Product / Object",
    "Digital / Physical Hybrid",
    "Research / Prototype",
    "Other",
}

PROJECT_TERMS = re.compile(
    r"\b(project|projects|work|works|portfolio|artwork|artworks|mural|murals|"
    r"installation|installations|sculpture|sculptures|object|objects|exhibition|"
    r"exhibitions|fabrication|research|prototype|public art|gallery|monument|"
    r"memorial|environment|facade|projection)\b",
    re.I,
)
# Commerce terms (store/shop/product) are intentionally absent: artist stores can hold artworks.
SKIP_TERMS = re.compile(
    r"\b(about|bio|biography|book|cart|category|checkout|coming soon|contact|cv|events?|"
    r"feed|lectures?|login|map|newsletter|press|privacy|tag|timeline|terms|"
    r"mailing list|cookie)\b",
    re.I,
)
ASSET_EXTENSIONS = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|pdf|zip|mp4|mov|avi|mp3|wav|docx?|xlsx?)$",
    re.I,
)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
DIMENSION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ft\.?|feet|in\.?|inches|cm|mm|[”\"']|â€)?\s*"
    r"(?:x|×|Ã—|by)\s*\d+(?:\.\d+)?(?:\s*(?:ft\.?|feet|in\.?|inches|cm|mm|[”\"']|â€))?"
    r"(?:\s*(?:x|×|Ã—|by)\s*\d+(?:\.\d+)?\s*(?:ft\.?|feet|in\.?|inches|cm|mm|[”\"']|â€))?"
    r"|"
    r"\b\d+(?:\.\d+)?\s*(?:x|×|Ã—)\s*\d+(?:\.\d+)?\s*(?:ft\.?|feet|in\.?|inches|cm|mm|[”\"']|â€)?",
    re.I,
)
LOCATION_RE = re.compile(
    r"\b([A-Z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*){0,3},\s*"
    r"(?:MA|NJ|NM|NY|CA|TN|KS|RI|CT|VT|NH|ME|"
    r"Puerto Rico|Colombia|USA))\b"
)
QUOTED_TITLE_RE = re.compile(r"^[\s“”\"']*([^“”\"']{2,100}?)[\s“”\"']*(?:[,.]|$)")

MATERIAL_RULES = {
    "acrylic paint": (r"\bacrylic(?: vinyl| latex)? paint\b|\bacrylic\b",),
    "spray paint": (r"\bspray paint\b",),
    "wood": (r"\bwood(?:en)?\b",),
    "steel": (r"\bsteel\b",),
    "fabric": (r"\bfabric\b|\btextile\b",),
    "light": (r"\blight(?:ing)?\b|\bled\b",),
    "projection": (r"\bprojection\b",),
    "video": (r"\bvideo\b",),
    "glass": (r"\bglass\b",),
    "concrete": (r"\bconcrete\b",),
    "paint": (r"\bpaint(?:ed|ing)?\b",),
    "found objects": (r"\bfound objects?\b",),
    "digital media": (r"\bdigital media\b|\bcomputational\b",),
    "paper": (r"\bpaper\b",),
    "canvas": (r"\bcanvas\b",),
    "vinyl": (r"\bvinyl\b",),
    "metal": (r"\bmetal\b",),
    "plastic": (r"\bplastic\b|\bacrylic sheet\b",),
    "mixed media": (r"\bmixed media\b",),
}

METHOD_RULES = {
    "hand-painted": (r"\bhand[ -]?painted\b",),
    "spray-painted": (r"\bspray[ -]?painted\b|\bspray paint\b",),
    "fabricated": (r"\bfabricat(?:ed|ion)\b",),
    "CNC cut": (r"\bcnc\b",),
    "laser cut": (r"\blaser[ -]?cut\b",),
    "projection mapped": (r"\bprojection map(?:ped|ping)\b",),
    "digitally modeled": (r"\bdigitally modeled\b|\b3d model(?:ed|ing)?\b",),
    "assembled": (r"\bassembl(?:ed|y)\b",),
    "welded": (r"\bweld(?:ed|ing)\b",),
    "carved": (r"\bcarv(?:ed|ing)\b",),
    "printed": (r"\bprint(?:ed|ing)\b",),
    "installed": (r"\binstall(?:ed|ation)\b",),
    "painted": (r"\bpaint(?:ed|ing)\b|\bacrylic\b",),
    "drawn": (r"\bdraw(?:n|ing)\b",),
    "designed": (r"\bdesign(?:ed|ing)\b",),
    "constructed": (r"\bconstruct(?:ed|ion)\b|\bbuilt\b",),
}

CONTEXT_RULES = {
    "school": (r"\bschool\b|\bacademy\b|\bmontessori\b",),
    "museum": (r"\bmuseum\b",),
    "gallery": (r"\bgallery\b",),
    "public park": (r"\bpublic park\b|\bpark\b",),
    "street": (r"\bstreet\b",),
    "underpass": (r"\bunderpass\b",),
    "transit": (r"\btransit\b|\bstation\b",),
    "civic": (r"\bcivic\b|\btown of\b|\bcity of\b",),
    "community": (r"\bcommunity\b",),
    "hospitality": (r"\bhotel\b|\brestaurant\b|\bhospitality\b",),
    "residential": (r"\bresidential\b",),
    "commercial": (r"\bcommercial\b|\bretail\b",),
    "cultural institution": (r"\bcultural institution\b|\bmuseum\b|\bart association\b",),
    "festival": (r"\bfestival\b",),
    "temporary event": (r"\btemporary event\b|\bpop[ -]?up\b",),
    "public realm": (r"\bpublic art\b|\bpublic realm\b|\bmural\b|\bmonument\b",),
    "education": (r"\bschool\b|\bacademy\b|\buniversity\b|\bcollege\b",),
    "neighborhood": (r"\bneighbou?rhood\b",),
    "waterfront": (r"\bwaterfront\b|\bseawall\b",),
    "urban": (r"\burban\b|\bstreet\b",),
    "interior": (r"\binterior\b|\bindoor\b",),
    "exterior": (r"\bexterior\b|\boutdoor\b|\bwall\b|\bfacade\b",),
}


@dataclass
class Page:
    requested_url: str
    url: str
    status_code: int
    content_type: str
    html: str
    text: str
    title: str
    h1: str
    captions: list[str] = field(default_factory=list)
    image_alts: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    rendered: bool = False


@dataclass
class Candidate:
    title: str
    excerpt: str
    image_urls: list[str]
    source_granularity: str
    page_url: str
    page_text: str = ""
    detail_url: str = ""
    collection_url: str = ""
    year: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    review_flags: list[str] = field(default_factory=list)


@dataclass
class LogEntry:
    timestamp_utc: str
    url: str
    action: str
    status: str
    http_status: str = ""
    detail: str = ""


def clean_text(value: str | None) -> str:
    """Display-safe text cleaning (entities, smart quotes, whitespace). Preserves case/accents."""
    from burning_man_scraper.sources.artist_website.text_normalize import normalize_display_text

    return normalize_display_text(value)


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def normalize_url(url: str, base_url: str | None = None) -> str:
    """Artist-website URL identity (tracking strip, slash/fragment policy)."""
    from burning_man_scraper.sources.artist_website.text_normalize import normalize_identity_url

    return normalize_identity_url(url, base_url)


def registrable_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_internal_url(url: str, root_url: str) -> bool:
    return registrable_host(url) == registrable_host(root_url)


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    return session


def robots_parser(session: requests.Session, root_url: str, timeout: int) -> RobotFileParser:
    parsed = urlparse(root_url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = session.get(robots_url, timeout=timeout)
        if response.ok:
            parser.parse(response.text.splitlines())
        else:
            parser.parse([])
    except requests.RequestException:
        parser.parse([])
    return parser


def parse_html(requested_url: str, final_url: str, status: int, content_type: str, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    h1_tag = soup.find("h1")
    h1 = clean_text(h1_tag.get_text(" ", strip=True) if h1_tag else "")
    captions = unique(
        tag.get_text(" ", strip=True)
        for tag in soup.select(
            "figcaption, .caption, .image-caption, .gallery-caption, "
            ".gallery-caption-content, [class*='caption']"
        )
    )
    image_alts: list[str] = []
    image_urls: list[str] = []
    from burning_man_scraper.sources.artist_website.images import extract_images_from_soup

    for evidence in extract_images_from_soup(soup, final_url):
        if evidence.alt:
            image_alts.append(evidence.alt)
        image_urls.append(evidence.url)
    text = clean_text(soup.get_text(" ", strip=True))
    return Page(
        requested_url=requested_url,
        url=normalize_url(final_url),
        status_code=status,
        content_type=content_type,
        html=html,
        text=text,
        title=title,
        h1=h1,
        captions=captions,
        image_alts=unique(image_alts),
        image_urls=unique(image_urls),
    )


def render_page_if_needed(url: str, timeout: int = DEFAULT_TIMEOUT) -> Page | None:
    """Render a sparse page with Playwright when the optional dependency is installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        response = page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        html = page.content()
        final_url = page.url
        status = response.status if response else 200
        browser.close()
    parsed = parse_html(url, final_url, status, "text/html; rendered=playwright", html)
    parsed.rendered = True
    return parsed


def fetch_page(
    session: requests.Session,
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    use_playwright: bool = False,
) -> Page:
    response = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise ValueError(f"Unsupported content type: {content_type or 'unknown'}")
    declared_length = int(response.headers.get("Content-Length", "0") or 0)
    if declared_length > MAX_BODY_BYTES:
        raise ValueError(f"Page exceeds {MAX_BODY_BYTES} bytes")
    body = response.content
    if len(body) > MAX_BODY_BYTES:
        raise ValueError(f"Page exceeds {MAX_BODY_BYTES} bytes")
    response.encoding = response.encoding or response.apparent_encoding
    page = parse_html(url, response.url, response.status_code, content_type, response.text)
    from burning_man_scraper.sources.artist_website.render import (
        initial_render_reasons,
        rendered_is_richer,
    )

    render_reasons = initial_render_reasons(page)
    if use_playwright and render_reasons:
        rendered = render_page_if_needed(page.url, timeout)
        if rendered and rendered_is_richer(page, rendered):
            rendered_reasons = initial_render_reasons(rendered)
            # Post-render comparison only: keep rendered when richer.
            return rendered
    return page


def extract_links(page: Page, root_url: str) -> list[str]:
    soup = BeautifulSoup(page.html, "html.parser")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = normalize_url(href, page.url)
        if (
            urlparse(url).scheme in {"http", "https"}
            and is_internal_url(url, root_url)
            and not ASSET_EXTENSIONS.search(urlparse(url).path)
        ):
            links.append(url)
    return unique(links)


def page_project_score(page: Page) -> int:
    """Legacy gate used by detect_project_pages; commerce paths are no longer hard-rejected."""
    path = urlparse(page.url).path
    # Hard reject only clear utility pages (not store/shop/product).
    if re.search(
        r"\b(cart|checkout|login|privacy|terms|contact|cv|biography|bio|press|newsletter)\b",
        path,
        re.I,
    ):
        return -100
    if SKIP_TERMS.search(f"{page.title} {page.h1}"):
        # Allow store/gallery headings that also contain project terms
        if not PROJECT_TERMS.search(f"{page.title} {page.h1} {page.url}"):
            return -100
    searchable = " ".join([page.url, page.title, page.h1, *page.captions[:20]])
    score = len(PROJECT_TERMS.findall(searchable)) * 2
    if page.h1 and page.h1.casefold() not in {"work", "artwork", "projects", "portfolio"}:
        score += 2
    if page.captions:
        score += 2
    if page.image_urls:
        score += 1
    if re.search(r"\b(store|shop|product)\b", page.url, re.I):
        score += 1  # commerce may still hold artworks
    return score


def detect_project_pages(pages: Sequence[Page], project_index_url: str | None = None) -> list[Page]:
    index = normalize_url(project_index_url) if project_index_url else ""
    return [
        page
        for page in pages
        if page_project_score(page) >= 2 or (index and normalize_url(page.url) == index)
    ]


def likely_caption(text: str) -> bool:
    text = clean_text(text)
    return bool(
        4 <= len(text) <= 700
        and (
            YEAR_RE.search(text)
            or DIMENSION_RE.search(text)
            or PROJECT_TERMS.search(text)
            or re.match(r"^[“\"'][^”\"']+[”\"']", text)
        )
    )


def strong_caption(text: str) -> bool:
    text = clean_text(text)
    has_material = bool(generate_materials(text))
    has_explicit_format = bool(
        re.search(
            r"\b(murals?|installation|sculpture|exhibition|monument|memorial|"
            r"projection|facade|public art)\b",
            text,
            re.I,
        )
    )
    quoted_title = bool(re.match(r"^[“\"'][^”\"']+[”\"']", text))
    detailed_year = bool(YEAR_RE.search(text) and len(text.split()) >= 5)
    return bool(
        4 <= len(text) <= 700
        and (
            DIMENSION_RE.search(text)
            or has_material
            or quoted_title
            or (detailed_year and has_explicit_format)
        )
    )


def title_from_excerpt(excerpt: str) -> str:
    excerpt = re.sub(r"^\s*view\s+fullsize\s*", "", clean_text(excerpt), flags=re.I)
    match = re.match(r"^[“\"']([^”\"']{2,120})[”\"']", excerpt)
    if match:
        return clean_text(match.group(1).rstrip(".,"))
    before_year = YEAR_RE.split(excerpt, maxsplit=1)[0].strip(" ,.-")
    before_details = re.split(
        r"\b(?:collaboration with|for|at|acrylic|spray paint|site specific|"
        r"\d+(?:\.\d+)?\s*(?:ft|feet|in|inches))\b",
        before_year,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,.-")
    if 2 <= len(before_details) <= 120 and len(before_details.split()) <= 14:
        return before_details
    return ""


def artwork_to_candidate(artwork) -> Candidate:
    """Adapt ArtworkCandidate to the legacy Candidate row shape."""
    proof_url = artwork.detail_url or artwork.page_url or artwork.collection_url
    return Candidate(
        title=artwork.title,
        excerpt=artwork.excerpt or artwork.title,
        image_urls=list(artwork.image_urls),
        source_granularity=artwork.source_granularity,
        page_url=proof_url,
        page_text=artwork.page_text,
        detail_url=artwork.detail_url,
        collection_url=artwork.collection_url,
        year=artwork.year,
        metadata=dict(artwork.metadata),
        review_flags=list(artwork.review_flags),
    )


def candidate_from_block(block: Tag, page: Page) -> Candidate | None:
    """Legacy helper: build a candidate from a DOM block (internal links allowed)."""
    from burning_man_scraper.sources.artist_website.discover import (
        _local_text,
        title_from_card_text,
        normalize_detail_url,
        score_detail_url,
    )
    from burning_man_scraper.sources.artist_website.images import extract_image_from_tag

    text = re.sub(
        r"^\s*view\s+fullsize\s*",
        "",
        clean_text(block.get_text(" ", strip=True)),
        flags=re.I,
    )
    images = []
    for image in block.find_all("img"):
        evidence = extract_image_from_tag(image, page.url)
        if evidence:
            images.append(evidence)
    alts = [image.alt for image in images if image.alt]
    detail_url = ""
    for anchor in block.find_all("a", href=True):
        linked_url = normalize_url(anchor.get("href", ""), page.url)
        if is_internal_url(linked_url, page.url) and linked_url != normalize_url(page.url):
            if score_detail_url(linked_url, card_text=text) >= 2:
                detail_url = normalize_detail_url(linked_url, page.url)
                break
    title_source = text if text else next(iter(alts), "")
    title, flags = title_from_card_text(title_source)
    if not title and alts:
        title, flags = title_from_card_text(alts[0])
        flags.append("title_inferred_from_alt")
    if re.search(r"\.(?:jpe?g|png|gif|webp|tiff?)$", title, re.I):
        return None
    excerpt = text or next(iter(alts), title)
    if title and excerpt:
        return Candidate(
            title=title,
            excerpt=excerpt or title,
            image_urls=unique(image.url for image in images),
            source_granularity="Gallery caption",
            page_url=detail_url or page.url,
            page_text=page.text,
            detail_url=detail_url,
            collection_url=page.url,
            review_flags=flags,
        )
    return None


def split_collection_page_entries(page: Page, artist_name: str = "") -> list[Candidate]:
    from burning_man_scraper.sources.artist_website.discover import discover_collection_candidates

    return [
        artwork_to_candidate(item)
        for item in discover_collection_candidates(page, artist_name=artist_name)
        if item.title
    ]


def extract_project_entries(page: Page, artist_name: str = "") -> list[Candidate]:
    from burning_man_scraper.sources.artist_website.classify import classify_page
    from burning_man_scraper.sources.artist_website.extract import extract_detail_candidate

    interpretation = classify_page(page, artist_name=artist_name)
    if interpretation.page_type == "artwork_collection" or len(interpretation.candidates) >= 2:
        return [artwork_to_candidate(item) for item in interpretation.candidates if item.title]

    if interpretation.page_type == "artwork_detail":
        detail = extract_detail_candidate(page, artist_name=artist_name)
        if detail.title:
            return [artwork_to_candidate(detail)]

    # Fall back to legacy individual-page behavior for sparse project pages
    collection_entries = split_collection_page_entries(page, artist_name=artist_name)
    distinct_titles = {normalize_title(item.title) for item in collection_entries}
    if len(distinct_titles) >= 2:
        return collection_entries

    title = clean_text(page.h1)
    page_title = clean_text(page.title)
    branded_title = re.match(
        rf"^(.+?)\s*(?:—|â€”|\|)\s*{re.escape(title)}$",
        page_title,
        re.I,
    )
    if branded_title:
        title = clean_text(branded_title.group(1))
    generic = {"", "work", "artwork", "projects", "portfolio", "gallery"}
    if title.casefold() in generic:
        title = clean_text(re.split(r"\s*(?:—|â€”|\|)\s*", page_title, maxsplit=1)[0])
    if not title or title.casefold() in generic:
        return collection_entries
    if urlparse(page.url).path in {"", "/"} and not PROJECT_TERMS.search(title):
        return collection_entries

    evidence = next((caption for caption in page.captions if likely_caption(caption)), "")
    if not evidence:
        paragraphs = [
            clean_text(tag.get_text(" ", strip=True))
            for tag in BeautifulSoup(page.html, "html.parser").find_all("p")
        ]
        evidence = next((paragraph for paragraph in paragraphs if len(paragraph) >= 20), "")
    if evidence:
        granularity = "Individual project page"
        excerpt = evidence[:700]
    else:
        granularity = "Image-only inference"
        excerpt = title
    from burning_man_scraper.sources.artist_website.images import (
        extract_images_from_soup,
        prefer_artwork_images,
    )

    soup = BeautifulSoup(page.html, "html.parser")
    preferred = prefer_artwork_images(
        extract_images_from_soup(soup, page.url),
        artist_name=artist_name,
    )
    image_urls = [image.url for image in preferred] or list(page.image_urls)
    return [
        Candidate(
            title=title,
            excerpt=excerpt,
            image_urls=image_urls,
            source_granularity=granularity,
            page_url=page.url,
            page_text=page.text,
        )
    ]


def match_rules(text: str, rules: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        value
        for value, patterns in rules.items()
        if any(re.search(pattern, text, re.I) for pattern in patterns)
    ]


def classify_project_type(text: str) -> tuple[str, str, str]:
    lowered = text.casefold()
    mappings = [
        ("Art Vehicle / Art Car", r"\bart car\b|\bart vehicle\b|\bparade vehicle\b"),
        ("Interactive Installation", r"\binteractive\b|\bsensor\b|\bviewer-activated\b|\bresponsive\b"),
        ("Digital / Physical Hybrid", r"\bprojections?\b|\baugmented reality\b|\bvirtual reality\b|\bdigital.{0,30}physical\b"),
        ("Public Art", r"\bmurals?\b|\bpublic art\b|\bstreet art\b|\bunderpass\b|\bpublic realm\b"),
        ("Museum / Cultural Project", r"\bmuseum\b|\bcultural institution\b|\bpublic collection\b"),
        ("Experiential Environment", r"\bimmersive\b|\bexperiential\b|\bthemed space\b|\bvisitor environment\b"),
        ("Event / Temporary Activation", r"\bfestival\b|\bactivation\b|\bpop[ -]?up\b|\btemporary event\b"),
        ("Architectural Feature", r"\bfacade\b|\barchitectural intervention\b|\bbuilding-integrated\b"),
        ("Exhibition", r"\bexhibition\b|\bsolo show\b|\bgroup show\b|\bcurated presentation\b"),
        ("Installation", r"\binstallation\b|\bsite-specific\b|\bspatial artwork\b"),
        ("Sculpture", r"\bsculpture\b|\bsculptural\b|\bmonument\b|\bmemorial\b|\brelief\b"),
        ("Fabrication Project", r"\bfabrication\b|\bdesign-build\b|\bcnc\b|\blaser cut\b|\bshop-built\b"),
        (
            "Product / Object",
            r"\bfurniture\b|\bproduct\b|\bdesigned object\b|\bfunctional object\b|"
            r"\bsigned\s+prints?\b|\blimited\s+edition\b|\bedition\b|\bprints?\b",
        ),
        ("Research / Prototype", r"\bprototype\b|\bresearch\b|\bstudy\b|\bmaterial exploration\b|\bspeculative\b"),
    ]
    for project_type, pattern in mappings:
        if re.search(pattern, lowered, re.I):
            explicit = bool(re.search(pattern, text, re.I))
            return project_type, ("high" if explicit else "medium"), "scraper"
    return "Other", "low", "ai"


def generate_tags(text: str, project_type: str) -> list[str]:
    rules = {
        "mural": r"\bmural\b",
        "painting": r"\bpaint(?:ing|ed)?\b|\bacrylic\b",
        "drawing": r"\bdrawing\b",
        "public mural": r"\bpublic mural\b|\bmural\b",
        "projection": r"\bprojection\b",
        "projection mapping": r"\bprojection map",
        "immersive environment": r"\bimmersive\b",
        "site-specific work": r"\bsite[ -]?specific\b",
        "digital artwork": r"\bdigital art",
        "mixed media": r"\bmixed media\b",
        "community art": r"\bcommunity\b",
        "temporary installation": r"\btemporary installation\b",
        "permanent installation": r"\bpermanent installation\b",
        "public sculpture": r"\bpublic sculpture\b|\bmonument\b",
        "environmental artwork": r"\benvironmental art",
        "architectural intervention": r"\barchitectural intervention\b|\bfacade\b",
        "exhibition": r"\bexhibition\b",
        "installation": r"\binstallation\b",
        "art object": r"\bart object\b",
        "architectural model": r"\barchitectural model\b|\bmodel drawing\b",
        "spatial artwork": r"\bspatial\b",
        "public intervention": r"\bpublic intervention\b",
    }
    tags = [tag for tag, pattern in rules.items() if re.search(pattern, text, re.I)]
    if project_type == "Public Art" and "public mural" not in tags and re.search(r"\bmural\b", text, re.I):
        tags.append("public mural")
    return unique(tag.lower() for tag in tags)


def generate_materials(text: str) -> list[str]:
    materials = match_rules(text, MATERIAL_RULES)
    if len(materials) > 1 and "paint" in materials and any("paint" in item for item in materials if item != "paint"):
        materials.remove("paint")
    return materials


def generate_fabrication_methods(text: str) -> list[str]:
    return match_rules(text, METHOD_RULES)


def generate_context_tags(text: str) -> list[str]:
    return match_rules(text, CONTEXT_RULES)


def extract_year(text: str) -> str:
    years = YEAR_RE.findall(text)
    return years[-1] if years else ""


def extract_dimensions(text: str) -> str:
    from burning_man_scraper.sources.artist_website.text_normalize import normalize_dimension_text

    repaired = normalize_dimension_text(text)
    match = DIMENSION_RE.search(repaired) or DIMENSION_RE.search(text or "")
    return clean_text(match.group(0)) if match else ""


def extract_location(text: str) -> str:
    match = LOCATION_RE.search(text)
    return clean_text(match.group(1)) if match else ""


def extract_collaborators(text: str) -> list[str]:
    matches = re.findall(
        r"\bcollaboration with\s+([^.;]+?)(?=\s+(?:at|for|on|in)\b|[.;]|$)",
        text,
        re.I,
    )
    return unique(matches)


def extract_institution(text: str) -> str:
    patterns = [
        r"\b(?:at|for)\s+((?:the\s+)?[A-Z][^.;]{2,100}?(?:Museum|University|College|Academy|School|Foundation|Association))\b",
        r"\b([A-Z][^.;]{2,100}?(?:Museum|University|College|Academy|School|Foundation|Association))\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean_text(match.group(1))
    return ""


def extract_client(text: str) -> str:
    match = re.search(
        r"\bfor\s+([^.;]{2,100}?)(?=\s+(?:at|in)\b|[.;]|,\s*(?:19|20)\d{2}|$)",
        text,
        re.I,
    )
    return clean_text(match.group(1)) if match else ""


def generate_what_they_did(
    title: str, project_type: str, materials: Sequence[str], source_granularity: str
) -> str:
    if source_granularity == "Image-only inference":
        return f"Created a project titled {title}, documented through images on the artist's website."
    material_text = f" using {', '.join(materials)}" if materials else ""
    noun = {
        "Public Art": "public artwork",
        "Sculpture": "sculptural work",
        "Installation": "installation",
        "Interactive Installation": "interactive installation",
        "Exhibition": "exhibition project",
        "Architectural Feature": "architectural feature",
        "Digital / Physical Hybrid": "digital and physical work",
        "Product / Object": "object",
    }.get(project_type, "art project")
    return f"Created {title}, a {noun}{material_text}."


def generate_why_it_matters(
    project_type: str, context_tags: Sequence[str], source_granularity: str
) -> str:
    if source_granularity == "Image-only inference":
        return "The project documents the artist's practice, but the available artist-site text provides limited context."
    if project_type == "Public Art":
        setting = f" in a {context_tags[0]} setting" if context_tags else ""
        return f"The project contributes a visible artwork{setting} and documents the artist's public practice."
    if context_tags:
        return f"The project documents the artist's work in a {context_tags[0]} context."
    return "The project documents the artist's practice, while the available artist-site text provides limited contextual detail."


def calculate_confidence(candidate: Candidate, project_type: str) -> tuple[str, str, str]:
    if candidate.source_granularity == "Image-only inference":
        return "Low", "low", "low"
    explicit_detail = bool(
        YEAR_RE.search(candidate.excerpt)
        or DIMENSION_RE.search(candidate.excerpt)
        or generate_materials(candidate.excerpt)
    )
    proof = "High" if candidate.source_granularity == "Individual project page" else "Medium"
    classification = "high" if project_type != "Other" and PROJECT_TERMS.search(candidate.excerpt) else "medium"
    description = "high" if explicit_detail else "medium"
    return proof, classification, description


def normalize_title(title: str) -> str:
    """Match/dedup key only — never write into human-facing project_title."""
    from burning_man_scraper.sources.artist_website.text_normalize import normalize_match_key

    return normalize_match_key(title)


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")


def artist_named_output_path(filename: str | None, artist_name: str) -> Path:
    artist_slug = slugify(artist_name) or "artist"
    path = Path(filename or "artelier_bulk_import.csv")
    if artist_slug not in path.stem.casefold():
        path = path.with_name(f"{artist_slug}_{path.name}")
    return path


def candidate_to_row(candidate: Candidate, artist_name: str, artist_website: str) -> dict[str, object]:
    from burning_man_scraper.sources.artist_website.confidence import apply_conservative_project_type

    evidence = clean_text(f"{candidate.title}. {candidate.excerpt}")
    if candidate.metadata:
        evidence = clean_text(
            f"{evidence} " + " ".join(f"{key} {value}" for key, value in candidate.metadata.items())
        )
    classification_evidence = evidence
    if candidate.source_granularity == "Gallery caption":
        classification_evidence = clean_text(f"{evidence} {candidate.page_text[:500]}")
    project_type, mapped_confidence, classification_source = classify_project_type(
        classification_evidence
    )
    materials = generate_materials(evidence)
    methods = generate_fabrication_methods(evidence)
    contexts = generate_context_tags(evidence)
    tags = generate_tags(classification_evidence, project_type)
    collaborators = extract_collaborators(evidence)
    proof_confidence, classification_confidence, description_confidence = calculate_confidence(
        candidate, project_type
    )
    project_type, mapped_confidence = apply_conservative_project_type(
        project_type,
        mapped_confidence=mapped_confidence,
        flags=list(candidate.review_flags or []),
    )
    if mapped_confidence == "low":
        classification_confidence = "low"
    if candidate.review_flags and any(
        flag in candidate.review_flags
        for flag in ("low_confidence_entity", "collection_only", "title_inferred_from_slug", "weak_project_type")
    ):
        proof_confidence = "Low"
        classification_confidence = "low"
    notes: list[str] = []
    if candidate.source_granularity == "Image-only inference":
        notes.append("Sparse image-led page; title is explicit but project details require human review.")
    if project_type == "Other":
        notes.append("Project type could not be supported by explicit website language.")
    if candidate.review_flags:
        notes.append("flags:" + ",".join(candidate.review_flags))
    proof_url = candidate.detail_url or candidate.page_url
    year = candidate.year or extract_year(evidence)
    dimensions = candidate.metadata.get("dimensions") or extract_dimensions(evidence)
    return {
        "artist_name": artist_name,
        "artist_website": normalize_url(artist_website),
        "project_title": candidate.title,
        "proof_title": candidate.title,
        "proof_external_url": proof_url,
        "proof_excerpt": candidate.excerpt[:700],
        "source_granularity": candidate.source_granularity,
        "project_type": project_type,
        "tags": tags,
        "materials": materials,
        "fabrication_methods": methods,
        "context_tags": contexts,
        "what_they_did": generate_what_they_did(
            candidate.title, project_type, materials, candidate.source_granularity
        ),
        "why_it_matters": generate_why_it_matters(
            project_type, contexts, candidate.source_granularity
        ),
        "contributor_role": "Artist",
        "collaboration_status": "Collaborative project" if collaborators else "Solo project",
        "collaborators": "; ".join(collaborators),
        "location": extract_location(evidence),
        "year": year,
        "dimensions": dimensions,
        "client_or_commissioner": extract_client(evidence),
        "institution": extract_institution(evidence),
        "image_urls": candidate.image_urls,
        "proof_confidence": proof_confidence,
        "classification_confidence": classification_confidence,
        "description_confidence": description_confidence,
        "classification_source": classification_source,
        "review_status": "Needs review",
        "permission_status": "Needs permission",
        "import_notes": " ".join(notes),
        "collection_url": candidate.collection_url,
        "review_flags": list(candidate.review_flags),
        "price": candidate.metadata.get("price", ""),
        "availability": candidate.metadata.get("availability", ""),
        "series": candidate.metadata.get("series", ""),
        "inventory": candidate.metadata.get("inventory", ""),
        "medium": candidate.metadata.get("medium", ""),
    }


def validate_row(row: dict[str, object]) -> None:
    if row["project_type"] not in PROJECT_TYPES:
        raise ValueError(f"Invalid project_type: {row['project_type']}")
    for field_name in INTERNAL_ARRAY_FIELDS:
        value = row.get(field_name)
        if not isinstance(value, list):
            raise ValueError(f"{field_name} must be a list before CSV serialization")


def canonical_export_row(row: dict[str, object]) -> dict[str, object]:
    image_urls = row.get("image_urls", [])
    if not isinstance(image_urls, list):
        image_urls = []
    from burning_man_scraper.sources.artist_website.images import preferred_hero_url

    artist_name = clean_text(str(row.get("artist_name", "")))
    hero = preferred_hero_url(
        [str(url) for url in image_urls if url],
        artist_name=artist_name,
    )
    role_title = clean_text(str(row.get("contributor_role", "")))
    collaboration_status = clean_text(str(row.get("collaboration_status", "")))
    if collaboration_status == "Solo project":
        contribution_title = "Primary artist and creator"
    elif role_title:
        contribution_title = f"{role_title} contribution"
    else:
        contribution_title = "Project contribution"

    proof_url = clean_text(str(row.get("proof_external_url", "")))
    what_they_did = clean_text(str(row.get("what_they_did", "")))
    return {
        "project_title": clean_text(str(row.get("project_title", ""))),
        "project_slug": slugify(str(row.get("project_title", ""))),
        "project_type": clean_text(str(row.get("project_type", ""))),
        "project_year": clean_text(str(row.get("year", ""))),
        "project_location": clean_text(str(row.get("location", ""))),
        "project_summary": what_they_did,
        "client_name": clean_text(str(row.get("client_or_commissioner", ""))),
        "public_reference_url": proof_url,
        "hero_image_url": hero,
        "project_visibility": "private",
        "contributor_name": artist_name,
        "contributor_slug": slugify(artist_name),
        "role_title": role_title,
        "contributor_email": "",
        "contributor_website": clean_text(str(row.get("artist_website", ""))),
        "contributor_visibility": "private",
        "contribution_category": "",
        "contribution_title": contribution_title,
        "what_they_did": what_they_did,
        "why_it_mattered": clean_text(str(row.get("why_it_matters", ""))),
        "public_credit_language": "",
        "phase": "",
        "verification_status": "documented",
        "approval_status": "draft",
        "contribution_visibility": "private",
        "proof_title": clean_text(str(row.get("proof_title", ""))),
        "proof_type": clean_text(str(row.get("source_granularity", ""))),
        "proof_external_url": proof_url,
        "proof_description": clean_text(str(row.get("proof_excerpt", ""))),
        "proof_visibility": "private",
        "permission_status": "pending_permission",
    }


def load_template_columns(template_path: Path | None) -> list[str]:
    if not template_path:
        return CSV_COLUMNS
    with template_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        columns = next(reader, [])
    if not columns:
        raise ValueError(f"Template has no header: {template_path}")
    if columns != CSV_COLUMNS:
        raise ValueError("Template header does not match the Artelier canonical bulk import schema")
    return CSV_COLUMNS


def write_dict_csv(path: Path, rows: Sequence[dict[str, object]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_csv_outputs(
    rows: Sequence[dict[str, object]],
    logs: Sequence[LogEntry],
    output_path: Path,
    template_path: Path | None = None,
    artist_slug: str = "artist",
) -> tuple[Path, Path, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = load_template_columns(template_path)
    canonical_rows = [canonical_export_row(row) for row in rows]
    write_dict_csv(output_path, canonical_rows, columns)
    review_path = output_path.parent / f"{artist_slug}_artelier_review_queue.csv"
    review_rows = [
        canonical
        for original, canonical in zip(rows, canonical_rows)
        if original["review_status"] != "Approved"
        or original["proof_confidence"] == "Low"
        or original["classification_confidence"] == "low"
        or original["description_confidence"] == "low"
    ]
    write_dict_csv(review_path, review_rows, columns)
    log_path = output_path.parent / f"{artist_slug}_artelier_scrape_log.csv"
    write_dict_csv(
        log_path,
        [asdict(entry) for entry in logs],
        ["timestamp_utc", "url", "action", "status", "http_status", "detail"],
    )
    return output_path, log_path, review_path


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def unique_run_directory(base_directory: Path, artist_slug: str) -> Path:
    stamp = run_timestamp()
    run_directory = base_directory / f"{artist_slug}_{stamp}"
    suffix = 2
    while run_directory.exists():
        run_directory = base_directory / f"{artist_slug}_{stamp}_{suffix}"
        suffix += 1
    return run_directory


def raw_page_stem(url: str) -> str:
    parsed = urlparse(url)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", f"{parsed.netloc}{parsed.path}").strip("-").lower()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:100] or 'home'}-{digest}"


def save_raw_page(page: Page, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    stem = raw_page_stem(page.url)
    (raw_dir / f"{stem}.html").write_text(page.html, encoding="utf-8")
    debug = {
        "requested_url": page.requested_url,
        "final_url": page.url,
        "status_code": page.status_code,
        "content_type": page.content_type,
        "rendered": page.rendered,
        "page_title": page.title,
        "h1": page.h1,
        "captions": page.captions,
        "image_alt_text": page.image_alts,
        "image_urls": page.image_urls,
        "body_text": page.text,
    }
    (raw_dir / f"{stem}.json").write_text(
        json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def crawl_site(
    artist_url: str,
    project_index_url: str | None,
    output_dir: Path,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY,
    timeout: int = DEFAULT_TIMEOUT,
    use_playwright: bool = False,
) -> tuple[list[Page], list[LogEntry]]:
    from burning_man_scraper.sources.artist_website.discover import (
        discover_collection_candidates,
        normalize_detail_url,
        score_detail_url,
    )

    root_url = normalize_url(artist_url)
    start_urls = unique([root_url, normalize_url(project_index_url) if project_index_url else ""])
    queue = deque(start_urls)
    queued = set(start_urls)
    visited: set[str] = set()
    pages: list[Page] = []
    logs: list[LogEntry] = []
    session = make_session()
    robots = robots_parser(session, root_url, timeout)
    last_request = 0.0
    pending_details: list[str] = []

    def enqueue(link: str, *, priority: bool = False) -> None:
        if link in visited or link in queued:
            return
        queued.add(link)
        if priority:
            queue.appendleft(link)
        else:
            queue.append(link)

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if not robots.can_fetch(USER_AGENT, url):
            logs.append(LogEntry(timestamp(), url, "skip", "blocked", detail="robots.txt"))
            continue
        wait = delay - (time.monotonic() - last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            page = fetch_page(session, url, timeout, use_playwright)
            last_request = time.monotonic()
            if not is_internal_url(page.url, root_url):
                logs.append(
                    LogEntry(timestamp(), url, "skip", "redirected_external", str(page.status_code), page.url)
                )
                continue
            pages.append(page)
            save_raw_page(page, output_dir / "raw_pages")
            logs.append(
                LogEntry(
                    timestamp(),
                    page.url,
                    "crawl",
                    "parsed",
                    str(page.status_code),
                    f"{len(page.text)} text chars; {len(page.image_urls)} images"
                    + ("; rendered" if page.rendered else ""),
                )
            )
            # Discover detail URLs from this page and prioritize them
            for candidate in discover_collection_candidates(page):
                if not candidate.detail_url:
                    continue
                detail = normalize_detail_url(candidate.detail_url)
                if detail not in visited and detail not in queued:
                    pending_details.append(detail)
                    enqueue(detail, priority=True)
                    logs.append(
                        LogEntry(
                            timestamp(),
                            detail,
                            "discover",
                            "queued_detail",
                            detail=f"from {page.url}",
                        )
                    )
            for link in extract_links(page, root_url):
                priority = score_detail_url(link) >= 4
                enqueue(link, priority=priority)
        except (requests.RequestException, ValueError) as error:
            last_request = time.monotonic()
            logs.append(LogEntry(timestamp(), url, "crawl", "failed", detail=str(error)))

    # Follow-up: any still-missing prioritized details within remaining budget
    for detail in pending_details:
        if len(visited) >= max_pages:
            logs.append(
                LogEntry(
                    timestamp(),
                    detail,
                    "discover",
                    "detail_fetch_skipped_budget",
                    detail=f"max_pages={max_pages}",
                )
            )
            continue
        if detail in visited:
            continue
        enqueue(detail, priority=True)

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if not robots.can_fetch(USER_AGENT, url):
            logs.append(LogEntry(timestamp(), url, "skip", "blocked", detail="robots.txt"))
            continue
        wait = delay - (time.monotonic() - last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            page = fetch_page(session, url, timeout, use_playwright)
            last_request = time.monotonic()
            if not is_internal_url(page.url, root_url):
                continue
            pages.append(page)
            save_raw_page(page, output_dir / "raw_pages")
            logs.append(
                LogEntry(
                    timestamp(),
                    page.url,
                    "crawl",
                    "parsed",
                    str(page.status_code),
                    "priority_detail_followup",
                )
            )
        except (requests.RequestException, ValueError) as error:
            last_request = time.monotonic()
            logs.append(LogEntry(timestamp(), url, "crawl", "failed", detail=str(error)))

    if queue:
        for leftover in list(queue)[:20]:
            if score_detail_url(leftover) >= 2:
                logs.append(
                    LogEntry(
                        timestamp(),
                        leftover,
                        "discover",
                        "detail_fetch_skipped_budget",
                        detail=f"max_pages={max_pages}",
                    )
                )
        logs.append(
            LogEntry(
                timestamp(),
                root_url,
                "crawl",
                "stopped",
                detail=f"Reached max-pages limit ({max_pages}); {len(queue)} URLs remained queued.",
            )
        )
    return pages, logs


def deduplicate_rows(rows: Sequence[dict[str, object]], logs: list[LogEntry]) -> list[dict[str, object]]:
    deduped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        key = (
            normalize_title(str(row["project_title"])),
            normalize_url(str(row["proof_external_url"])),
        )
        current = deduped.get(key)
        if current is None:
            deduped[key] = row
            continue
        current_score = len(str(current["proof_excerpt"])) + len(current["image_urls"]) * 50
        new_score = len(str(row["proof_excerpt"])) + len(row["image_urls"]) * 50
        if new_score > current_score:
            deduped[key] = row
        logs.append(
            LogEntry(
                timestamp(),
                str(row["proof_external_url"]),
                "deduplicate",
                "skipped",
                detail=f"Duplicate title and proof URL: {row['project_title']}",
            )
        )
    return list(deduped.values())


def run_ingestion(args: argparse.Namespace) -> tuple[Path, Path, Path, int]:
    artist_slug = slugify(args.artist) or "artist"
    requested_output = artist_named_output_path(args.out, args.artist)
    base_output = Path(args.output_dir or ".").resolve()
    output_path = unique_run_directory(base_output, artist_slug) / requested_output.name
    output_dir = output_path.parent
    pages, logs = crawl_site(
        args.url,
        args.project_index,
        output_dir,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
        use_playwright=args.playwright,
    )
    project_pages = detect_project_pages(pages, args.project_index)
    from burning_man_scraper.sources.artist_website.pipeline import extract_site_artworks

    artworks = extract_site_artworks(pages, artist_name=args.artist, logs=logs)
    rows = [
        candidate_to_row(artwork_to_candidate(item), args.artist, args.url)
        for item in artworks
        if item.title
    ]
    if not rows:
        # Fallback for sites that only match legacy project-page scoring
        for page in project_pages:
            for candidate in extract_project_entries(page, artist_name=args.artist):
                rows.append(candidate_to_row(candidate, args.artist, args.url))
    rows = deduplicate_rows(rows, logs)
    for row in rows:
        validate_row(row)
    paths = write_csv_outputs(
        rows,
        logs,
        output_path,
        Path(args.template).resolve() if args.template else None,
        artist_slug,
    )

    # Future Supabase upload logic belongs after CSV review/approval, not in this ingestion pass.
    return *paths, len(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl an artist-owned website and create review-first registry CSVs."
    )
    parser.add_argument("--artist", required=True, help="Artist name")
    parser.add_argument("--url", required=True, help="Artist website URL")
    parser.add_argument("--project-index", help="Optional portfolio/project index URL")
    parser.add_argument("--template", help="Optional existing CSV template path")
    parser.add_argument("--output-dir", help="Optional output directory")
    parser.add_argument(
        "--out",
        help="Optional main CSV name; the artist slug is prepended when missing",
    )
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Seconds between requests")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--playwright",
        action="store_true",
        help="Use optional Playwright fallback for sparse JavaScript-rendered pages",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output, log_path, review_path, count = run_ingestion(args)
    print(f"Wrote {count} records to {output}")
    print(f"Wrote crawl log to {log_path}")
    print(f"Wrote review queue to {review_path}")
    print(f"Saved raw pages under {output.parent / 'raw_pages'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
