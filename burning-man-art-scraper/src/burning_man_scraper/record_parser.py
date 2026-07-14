from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.inline_archive import InlineArchiveRecord, extract_inline_archive_records, is_donate_link_text
from burning_man_scraper.models import InstallationRecord, SCHEMA_VERSION
from burning_man_scraper.state import detect_year, hash_value
from burning_man_scraper.url_utils import ALLOWED_HOSTNAME, normalize_url


RECORD_PARSER_VERSION = "phase4-installation-preview-v1"


@dataclass(frozen=True)
class ParsePreview:
    record: InstallationRecord
    source_position: int


@dataclass
class ParsedExternalLink:
    href: str
    class_name: str | None = None
    text_parts: list[str] | None = None

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in (self.text_parts or []) if part.strip())


class _InstallationHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.class_stack: list[set[str]] = []
        self.id_stack: list[str | None] = []
        self.text_by_key: dict[str, list[str]] = {}
        self.title_parts: list[str] = []
        self.canonical_url: str | None = None
        self.image_urls: list[str] = []
        self.image_alt_texts: list[str] = []
        self.links: list[ParsedExternalLink] = []
        self.anchor_stack: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value for key, value in attrs}
        classes = set((attr_map.get("class") or "").lower().split())
        element_id = attr_map.get("id")
        self.stack.append(tag.lower())
        self.class_stack.append(classes)
        self.id_stack.append(element_id.lower() if element_id else None)

        if tag.lower() == "link" and attr_map.get("rel") == "canonical":
            self.canonical_url = attr_map.get("href")
        if tag.lower() == "img" and attr_map.get("src"):
            self.image_urls.append(attr_map["src"] or "")
            if attr_map.get("alt"):
                self.image_alt_texts.append(attr_map["alt"] or "")
        if tag.lower() == "a" and attr_map.get("href"):
            self.links.append(
                ParsedExternalLink(
                    href=attr_map["href"] or "",
                    class_name=attr_map.get("class"),
                    text_parts=[],
                )
            )
            self.anchor_stack.append(len(self.links) - 1)

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()
        if self.class_stack:
            self.class_stack.pop()
        if self.id_stack:
            self.id_stack.pop()
        if tag.lower() == "a" and self.anchor_stack:
            self.anchor_stack.pop()

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if not text:
            return
        if self.stack and self.stack[-1] == "title":
            self.title_parts.append(text)
        if self.anchor_stack:
            link = self.links[self.anchor_stack[-1]]
            if link.text_parts is not None:
                link.text_parts.append(text)

        for key in active_keys(self.stack, self.class_stack, self.id_stack):
            self.text_by_key.setdefault(key, []).append(text)

    def text(self, key: str) -> str | None:
        value = clean_text(" ".join(self.text_by_key.get(key, [])))
        return value or None

    @property
    def page_title(self) -> str | None:
        title = clean_text(" ".join(self.title_parts))
        return title or None


def parse_installation_record(
    fetch_result: FetchResult,
    source_archive_url: str,
    source_position: int,
    scrape_run_id: str | None = None,
) -> ParsePreview:
    parser = _InstallationHTMLParser()
    errors: list[str] = []
    try:
        parser.feed(fetch_result.text)
    except Exception as exc:
        errors.append(str(exc))

    final_url = fetch_result.final_url
    canonical_source_url = (
        normalize_url(urljoin(final_url, parser.canonical_url)) if parser.canonical_url else normalize_url(final_url)
    )
    title = first_present(parser.text("title"), parser.text("h1"), strip_site_suffix(parser.page_title))
    artist_display_text = parser.text("artist")
    image_urls = ordered_unique(
        normalize_url(urljoin(final_url, image_url)) for image_url in parser.image_urls
    )
    external_links = ordered_unique(
        normalize_url(urljoin(final_url, link.href))
        for link in parser.links
        if is_external_http_url(urljoin(final_url, link.href))
        and not is_donate_link_text(link.text, link.href, link.class_name)
    )

    record = InstallationRecord(
        record_id=hash_value(canonical_source_url)[:16],
        source_url=fetch_result.final_url,
        canonical_source_url=canonical_source_url,
        source_archive_url=source_archive_url,
        source_accessed_at=fetch_result.fetched_timestamp,
        scrape_run_id=scrape_run_id or str(uuid4()),
        scraped_at=utc_now(),
        parser_version=RECORD_PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        title=title,
        normalized_title=normalize_title(title) if title else None,
        year=first_present(parser.text("year"), detect_year(source_archive_url), detect_year(final_url)),
        event_name=parser.text("event_name"),
        event_theme=parser.text("event_theme"),
        installation_type=parser.text("installation_type"),
        honoraria_status=parser.text("honoraria_status"),
        funding_status=parser.text("funding_status"),
        artist_display_text=artist_display_text,
        artist_names=parse_artist_names(artist_display_text),
        artist_collective=parser.text("artist_collective"),
        artist_location=parser.text("artist_location"),
        description=parser.text("description"),
        materials=parser.text("materials"),
        dimensions=parser.text("dimensions"),
        location_on_playa=parser.text("location_on_playa"),
        website_url=first_external_url(external_links),
        project_url=parser.text("project_url"),
        external_links=external_links,
        image_urls=image_urls,
        primary_image_url=image_urls[0] if image_urls else None,
        image_alt_text=parser.image_alt_texts[0] if parser.image_alt_texts else None,
        photographer_credit=parser.text("photographer_credit"),
        image_credit_text=parser.text("image_credit_text"),
        parsing_errors=errors,
    )
    finalized = finalize_record(record)
    if not finalized.title:
        finalized.parsing_errors.append("Missing required preview title.")
    return ParsePreview(record=finalized, source_position=source_position)


def parse_inline_archive_record(
    fetch_result: FetchResult,
    source_archive_url: str,
    source_position: int,
    scrape_run_id: str | None = None,
) -> ParsePreview:
    inline_records = extract_inline_archive_records(
        fetch_result.text,
        archive_url=source_archive_url,
        final_url=fetch_result.final_url,
    )
    matching = next(
        (record for record in inline_records if record.source_position == source_position),
        None,
    )
    if matching is None:
        record = InstallationRecord(
            source_url=fetch_result.final_url,
            source_archive_url=source_archive_url,
            source_accessed_at=fetch_result.fetched_timestamp,
            scrape_run_id=scrape_run_id or str(uuid4()),
            scraped_at=utc_now(),
            parser_version=RECORD_PARSER_VERSION,
            schema_version=SCHEMA_VERSION,
            parsing_errors=[f"No inline archive record found at source position {source_position}."],
        )
        return ParsePreview(record=finalize_record(record), source_position=source_position)
    return inline_archive_record_to_preview(
        matching,
        fetch_result=fetch_result,
        source_archive_url=source_archive_url,
        scrape_run_id=scrape_run_id,
    )


def inline_archive_record_to_preview(
    inline_record: InlineArchiveRecord,
    fetch_result: FetchResult,
    source_archive_url: str,
    scrape_run_id: str | None = None,
) -> ParsePreview:
    external_links = [inline_record.website_url] if inline_record.website_url else []
    record = InstallationRecord(
        record_id=hash_value(inline_record.source_url)[:16],
        source_url=inline_record.source_url,
        canonical_source_url=inline_record.source_url,
        source_archive_url=source_archive_url,
        source_accessed_at=fetch_result.fetched_timestamp,
        scrape_run_id=scrape_run_id or str(uuid4()),
        scraped_at=utc_now(),
        parser_version=RECORD_PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        title=inline_record.title,
        normalized_title=normalize_title(inline_record.title),
        year=first_present(inline_record.year, detect_year(source_archive_url)),
        installation_type="Installation",
        artist_display_text=inline_record.artist_display_text,
        artist_names=parse_artist_names(inline_record.artist_display_text),
        artist_location=inline_record.artist_location,
        description=inline_record.description,
        website_url=inline_record.website_url,
        project_url=inline_record.website_url,
        external_links=external_links,
        image_urls=inline_record.image_urls,
        primary_image_url=inline_record.image_urls[0] if inline_record.image_urls else None,
        image_alt_text=inline_record.image_alt_text,
    )
    return ParsePreview(record=finalize_record(record), source_position=inline_record.source_position)


def finalize_record(record: InstallationRecord) -> InstallationRecord:
    required_preview_fields = [
        "title",
        "artist_display_text",
        "description",
        "primary_image_url",
        "photographer_credit",
    ]
    missing_fields = [
        field_name
        for field_name in required_preview_fields
        if getattr(record, field_name) in (None, "", [])
    ]
    warnings = list(record.warnings)
    if "artist_display_text" in missing_fields:
        warnings.append("Artist information was not found.")
    if "primary_image_url" in missing_fields:
        warnings.append("No image URL was found.")
    if "photographer_credit" in missing_fields:
        warnings.append("Photographer credit was not found.")

    populated = 0
    considered = 0
    for field_name, value in record.model_dump().items():
        if field_name in {"missing_fields", "warnings", "parsing_errors", "needs_manual_review"}:
            continue
        considered += 1
        if value not in (None, "", []):
            populated += 1

    confidence = round(populated / considered, 2) if considered else None
    return record.model_copy(
        update={
            "missing_fields": missing_fields,
            "warnings": warnings,
            "extraction_confidence": confidence,
            "needs_manual_review": bool(missing_fields or record.parsing_errors),
        }
    )


def active_keys(
    stack: list[str],
    class_stack: list[set[str]],
    id_stack: list[str | None],
) -> set[str]:
    keys: set[str] = set()
    for tag, classes, element_id in zip(stack, class_stack, id_stack):
        tokens = set(classes)
        if element_id:
            tokens.add(element_id)
        if tag == "h1":
            keys.add("h1")
            keys.add("title")
        token_map = {
            "title": "title",
            "installation-title": "title",
            "art-title": "title",
            "artist": "artist",
            "artists": "artist",
            "artist-name": "artist",
            "artist-collective": "artist_collective",
            "collective": "artist_collective",
            "artist-location": "artist_location",
            "description": "description",
            "materials": "materials",
            "dimensions": "dimensions",
            "location-on-playa": "location_on_playa",
            "playa-location": "location_on_playa",
            "photographer": "photographer_credit",
            "photographer-credit": "photographer_credit",
            "image-credit": "image_credit_text",
            "credit": "image_credit_text",
            "event-name": "event_name",
            "event-theme": "event_theme",
            "installation-type": "installation_type",
            "honoraria-status": "honoraria_status",
            "funding-status": "funding_status",
            "year": "year",
            "project-url": "project_url",
        }
        for token, key in token_map.items():
            if token in tokens:
                keys.add(key)
    return keys


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def strip_site_suffix(value: str | None) -> str | None:
    if not value:
        return None
    return clean_text(re.split(r"\s+[|]\s+|\s+-\s+", value, maxsplit=1)[0])


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_artist_names(value: str | None) -> list[str]:
    if not value:
        return []
    cleaned = re.sub(r"^(by|artist[s]?:)\s+", "", value, flags=re.IGNORECASE)
    parts = re.split(r"\s+(?:and|&)\s+|,\s*", cleaned)
    return [part.strip() for part in parts if part.strip()]


def first_present(*values: str | None) -> str | None:
    for value in values:
        cleaned = clean_text(value)
        if cleaned:
            return cleaned
    return None


def ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def is_external_http_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname != ALLOWED_HOSTNAME


def first_external_url(urls: list[str]) -> str | None:
    return urls[0] if urls else None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
