from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from burning_man_scraper.url_utils import ALLOWED_HOSTNAME, normalize_url


BLOCK_TAGS = {
    "article",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "li",
    "main",
    "p",
    "section",
}


@dataclass(frozen=True)
class InlineArchiveRecord:
    source_position: int
    source_url: str
    title: str
    artist_display_text: str | None
    artist_location: str | None
    year: str | None
    description: str | None
    website_url: str | None
    contact: str | None
    image_urls: list[str] = field(default_factory=list)
    image_alt_text: str | None = None


@dataclass(frozen=True)
class _Event:
    kind: str
    value: str
    href: str | None = None
    alt: str | None = None


class _ArchiveEventParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events: list[_Event] = []
        self.href_stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value for key, value in attrs}
        if tag.lower() in BLOCK_TAGS:
            self.events.append(_Event("break", ""))
        if tag.lower() == "a":
            self.href_stack.append(attr_map.get("href"))
        if tag.lower() == "img" and attr_map.get("src"):
            self.events.append(
                _Event(
                    "image",
                    attr_map.get("src") or "",
                    alt=attr_map.get("alt"),
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.href_stack:
            self.href_stack.pop()
        if tag.lower() in BLOCK_TAGS:
            self.events.append(_Event("break", ""))

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if not text:
            return
        href = self.href_stack[-1] if self.href_stack else None
        self.events.append(_Event("text", text, href=href))


def extract_inline_archive_records(html: str, archive_url: str, final_url: str) -> list[InlineArchiveRecord]:
    parser = _ArchiveEventParser()
    parser.feed(html)
    lines = coalesce_text_lines(parser.events)
    starts = find_record_starts(lines)
    records: list[InlineArchiveRecord] = []

    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        block = lines[start:end]
        if not block:
            continue
        title, title_href = block[0]
        source_url = inline_source_url(title_href, archive_url, final_url)
        image_urls, alt_text = images_between_titles(parser.events, title, starts, index, final_url)
        records.append(
            InlineArchiveRecord(
                source_position=len(records) + 1,
                source_url=source_url,
                title=title,
                artist_display_text=label_value(block, "by"),
                artist_location=label_value(block, "from"),
                year=label_value(block, "year"),
                description=description_text(block),
                website_url=url_label(block, "URL"),
                contact=label_value(block, "Contact"),
                image_urls=image_urls,
                image_alt_text=alt_text,
            )
        )

    return records


def coalesce_text_lines(events: list[_Event]) -> list[tuple[str, str | None]]:
    lines: list[tuple[str, str | None]] = []
    current: list[str] = []
    current_href: str | None = None
    for event in events:
        if event.kind == "break":
            flush_line(lines, current, current_href)
            current = []
            current_href = None
            continue
        if event.kind != "text":
            continue
        if current and event.href != current_href:
            flush_line(lines, current, current_href)
            current = []
        current.append(event.value)
        current_href = event.href
    flush_line(lines, current, current_href)
    return [(text, href) for text, href in lines if text]


def flush_line(lines: list[tuple[str, str | None]], current: list[str], href: str | None) -> None:
    text = clean_text(" ".join(current))
    if text:
        lines.append((text, href))


def find_record_starts(lines: list[tuple[str, str | None]]) -> list[int]:
    starts: list[int] = []
    for index, (text, href) in enumerate(lines):
        if is_metadata_line(text) or is_non_title_link_text(text, href) or text.lower().endswith("archive"):
            continue
        nearby = [line_text.lower() for line_text, _ in lines[index + 1 : index + 5]]
        if any(line.startswith("by:") for line in nearby) and any(
            line.startswith("year:") for line in nearby
        ):
            starts.append(index)
    return starts


def is_non_title_link_text(text: str, href: str | None) -> bool:
    lowered = text.lower()
    if is_donate_link_text(text, href):
        return True
    if href and href.lower().startswith(("mailto:", "tel:")):
        return True
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        return True
    if lowered.startswith(("http://", "https://")):
        return True
    return False


def is_donate_link_text(text: str | None, href: str | None = None, class_name: str | None = None) -> bool:
    combined = " ".join(value.lower() for value in (text, href, class_name) if value)
    if "donate to this project" in combined:
        return True
    if "donate" not in combined:
        return False
    href_value = (href or "").lower()
    class_value = (class_name or "").lower()
    return "donate" in class_value or "/donate" in href_value or "donation" in href_value


def label_value(block: list[tuple[str, str | None]], label: str) -> str | None:
    prefix = f"{label.lower()}:"
    for text, _href in block:
        if text.lower().startswith(prefix):
            return clean_text(text.split(":", 1)[1])
    return None


def url_label(block: list[tuple[str, str | None]], label: str) -> str | None:
    prefix = f"{label.lower()}:"
    for index, (text, href) in enumerate(block):
        if text.lower().startswith(prefix):
            if href:
                return href
            value = clean_text(text.split(":", 1)[1])
            if not value and index + 1 < len(block):
                next_text, next_href = block[index + 1]
                if next_href:
                    return next_href
                if next_text.lower().startswith(("http://", "https://")):
                    return next_text
            return value or None
    return None


def description_text(block: list[tuple[str, str | None]]) -> str | None:
    parts: list[str] = []
    seen_year = False
    for text, _href in block:
        lowered = text.lower()
        if lowered.startswith("year:"):
            seen_year = True
            continue
        if not seen_year:
            continue
        if lowered.startswith(("url:", "contact:")):
            break
        if is_metadata_line(text) or is_non_title_link_text(text, _href):
            continue
        parts.append(text)
    value = clean_text(" ".join(parts))
    return value or None


def is_metadata_line(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith(("by:", "from:", "year:", "url:", "contact:"))


def inline_source_url(href: str | None, archive_url: str, final_url: str) -> str:
    if not href:
        return archive_url
    absolute = urljoin(final_url, href)
    absolute_no_fragment, fragment = urldefrag(absolute)
    parsed = urlsplit(absolute_no_fragment)
    if parsed.hostname != ALLOWED_HOSTNAME:
        return archive_url
    normalized = normalize_url(absolute_no_fragment)
    return f"{normalized}#{fragment}" if fragment else normalized


def images_between_titles(
    events: list[_Event],
    title: str,
    starts: list[int],
    start_index: int,
    final_url: str,
) -> tuple[list[str], str | None]:
    title_seen = False
    images: list[str] = []
    alt_text: str | None = None
    next_title = None
    if start_index + 1 < len(starts):
        text_lines = coalesce_text_lines(events)
        next_title = text_lines[starts[start_index + 1]][0]

    for event in events:
        if event.kind == "text" and event.value == title:
            title_seen = True
            continue
        if title_seen and next_title and event.kind == "text" and event.value == next_title:
            break
        if title_seen and event.kind == "image":
            images.append(normalize_url(urljoin(final_url, event.value)))
            if event.alt and not alt_text:
                alt_text = event.alt
    return ordered_unique(images), alt_text


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()
