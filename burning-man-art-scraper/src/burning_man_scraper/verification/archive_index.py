from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin
from urllib.request import Request, urlopen

from burning_man_scraper.inline_archive import extract_inline_archive_records
from burning_man_scraper.record_parser import normalize_title
from burning_man_scraper.verification.models import ArchiveIndexRecord


ARCHIVE_URL_TEMPLATE = "https://history.burningman.org/art-history/archive/?yyyy={year}"
UID_PATTERN = re.compile(r"^a2I[A-Za-z0-9]+$")


def archive_url_for_year(year: int) -> str:
    return ARCHIVE_URL_TEMPLATE.format(year=year)


def fetch_archive_html(
    year: int,
    user_agent: str,
    timeout_seconds: float = 30.0,
) -> tuple[str, str]:
    url = archive_url_for_year(year)
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout_seconds) as response:
        html = response.read().decode("utf-8", errors="replace")
        return html, response.geturl()


def build_archive_index(
    year: int,
    user_agent: str,
    timeout_seconds: float = 30.0,
    html: str | None = None,
    final_url: str | None = None,
) -> list[ArchiveIndexRecord]:
    archive_url = archive_url_for_year(year)
    if html is None or final_url is None:
        html, final_url = fetch_archive_html(year, user_agent, timeout_seconds)

    inline_records = extract_inline_archive_records(html, archive_url, final_url)
    indexed: dict[str, ArchiveIndexRecord] = {}
    title_index: dict[str, ArchiveIndexRecord] = {}

    for inline in inline_records:
        uid = extract_uid(inline.source_url)
        normalized = normalize_title(inline.title)
        record = ArchiveIndexRecord(
            year=str(year),
            title=inline.title,
            normalized_title=normalized,
            artist_display_text=inline.artist_display_text,
            artist_location=inline.artist_location,
            description=inline.description,
            website_url=inline.website_url,
            canonical_source_url=inline.source_url,
            uid=uid,
            image_urls=list(inline.image_urls),
            image_alt_text=inline.image_alt_text,
        )
        if uid:
            existing = indexed.get(uid)
            if existing is None or _record_rank(record) > _record_rank(existing):
                indexed[uid] = record
        else:
            existing = title_index.get(normalized)
            if existing is None or _record_rank(record) > _record_rank(existing):
                title_index[normalized] = record

    combined = list(indexed.values())
    for normalized, record in title_index.items():
        if not any(item.normalized_title == normalized for item in combined):
            combined.append(record)
    combined.sort(key=lambda item: (item.normalized_title, item.title))
    return combined


def index_archive_by_uid(records: list[ArchiveIndexRecord]) -> dict[str, ArchiveIndexRecord]:
    return {record.uid: record for record in records if record.uid}


def index_archive_by_title(records: list[ArchiveIndexRecord]) -> dict[str, ArchiveIndexRecord]:
    index: dict[str, ArchiveIndexRecord] = {}
    for record in records:
        index.setdefault(record.normalized_title, record)
    return index


def extract_uid(source_url: str | None) -> str | None:
    if not source_url:
        return None
    _, fragment = urldefrag(source_url)
    if UID_PATTERN.fullmatch(fragment):
        return fragment
    return None


def _record_rank(record: ArchiveIndexRecord) -> int:
    score = 0
    if record.image_urls:
        score += 2
    if record.artist_display_text:
        score += 1
    if record.description:
        score += 1
    if record.uid:
        score += 1
    return score


def resolve_archive_record(
    *,
    uid: str | None,
    title: str | None,
    by_uid: dict[str, ArchiveIndexRecord],
    by_title: dict[str, ArchiveIndexRecord],
) -> ArchiveIndexRecord | None:
    if uid and uid in by_uid:
        return by_uid[uid]
    if title:
        normalized = normalize_title(title)
        if normalized in by_title:
            return by_title[normalized]
    return None
