from __future__ import annotations

from dataclasses import dataclass, field
import json
from urllib.parse import urldefrag

from burning_man_scraper.artelier_schema import ImportSchema, MappingConfig, build_artelier_preview
from burning_man_scraper.fetcher import BoundedFetcher, FetchResult
from burning_man_scraper.inspection import PageInspection
from burning_man_scraper.record_parser import (
    ParsePreview,
    parse_inline_archive_record,
    parse_installation_record,
)
from burning_man_scraper.state import ScraperState, SourceLookup
from burning_man_scraper.url_utils import normalize_url


BATCH_PARSER_VERSION = "phase7-approved-batch-v1"


@dataclass(frozen=True)
class ManifestChangeReport:
    new_links: list[str] = field(default_factory=list)
    removed_links: list[str] = field(default_factory=list)
    reordered_links: list[str] = field(default_factory=list)
    unchanged_links: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BatchResult:
    attempted: int
    succeeded: int
    failed: int
    skipped: int
    duplicates: int
    next_unprocessed_record: int | None
    completed_urls: list[str]
    failed_urls: list[str]
    skipped_urls: list[str]
    manifest_changes: ManifestChangeReport
    attempt_ceiling: int


def process_approved_batch(
    source_lookup: SourceLookup,
    inspection: PageInspection,
    source_result: FetchResult,
    fetcher: BoundedFetcher,
    state_store: ScraperState,
    requested_count: int,
    export_batch_id: int,
    preview_run_id: str,
    import_schema: ImportSchema | None = None,
    mapping_config: MappingConfig | None = None,
) -> BatchResult:
    existing_records = state_store.source_records_by_canonical(source_lookup.source.source_id)
    manifest_changes = compare_manifest(
        previous_records=existing_records,
        current_urls=inspection.candidate_installation_links,
    )
    attempt_ceiling = requested_count + max(1, requested_count // 5)
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0
    duplicates = 0
    completed_urls: list[str] = []
    failed_urls: list[str] = []
    skipped_urls: list[str] = []

    for source_position, candidate_url in enumerate(inspection.candidate_installation_links, start=1):
        if succeeded >= requested_count or attempted >= attempt_ceiling:
            break

        canonical_url = canonical_candidate_url(candidate_url)
        existing = existing_records.get(canonical_url)
        if existing and existing["record_status"] == "completed":
            skipped += 1
            duplicates += 1
            skipped_urls.append(candidate_url)
            state_store.mark_source_record_by_canonical(
                source_id=source_lookup.source.source_id,
                source_position=source_position,
                installation_url=candidate_url,
                canonical_installation_url=canonical_url,
                record_id=existing["record_id"],
                record_status="completed",
                content_hash=existing["content_hash"],
                export_batch_id=existing["export_batch_id"],
            )
            continue

        attempted += 1
        state_store.mark_source_record_by_canonical(
            source_id=source_lookup.source.source_id,
            source_position=source_position,
            installation_url=candidate_url,
            canonical_installation_url=canonical_url,
            record_id=existing["record_id"] if existing else None,
            record_status="processing",
            content_hash=existing["content_hash"] if existing else None,
            export_batch_id=export_batch_id,
        )

        try:
            parse_preview = parse_candidate(
                candidate_url=candidate_url,
                source_position=source_position,
                inspection=inspection,
                source_result=source_result,
                fetcher=fetcher,
                preview_run_id=preview_run_id,
            )
            if not is_valid_preview(parse_preview):
                failed += 1
                failed_urls.append(candidate_url)
                state_store.mark_source_record_by_canonical(
                    source_id=source_lookup.source.source_id,
                    source_position=source_position,
                    installation_url=candidate_url,
                    canonical_installation_url=parse_preview.record.canonical_source_url or canonical_url,
                    record_id=parse_preview.record.record_id,
                    record_status="failed",
                    content_hash=source_result.response_hash,
                    export_batch_id=export_batch_id,
                )
                continue

            record_canonical = parse_preview.record.canonical_source_url or canonical_url
            if state_store.completed_record_exists(source_lookup.source.source_id, record_canonical):
                skipped += 1
                duplicates += 1
                skipped_urls.append(candidate_url)
                continue

            state_store.mark_source_record_by_canonical(
                source_id=source_lookup.source.source_id,
                source_position=source_position,
                installation_url=candidate_url,
                canonical_installation_url=record_canonical,
                record_id=parse_preview.record.record_id,
                record_status="completed",
                content_hash=content_hash_for(parse_preview, source_result),
                export_batch_id=export_batch_id,
                record_json=json.dumps(parse_preview.record.model_dump(mode="json"), sort_keys=True),
                artelier_row_json=json.dumps(
                    build_artelier_preview(parse_preview.record, import_schema, mapping_config).row,
                    sort_keys=True,
                )
                if import_schema and mapping_config
                else None,
            )
            completed_urls.append(record_canonical)
            succeeded += 1
        except Exception:
            failed += 1
            failed_urls.append(candidate_url)
            state_store.mark_source_record_by_canonical(
                source_id=source_lookup.source.source_id,
                source_position=source_position,
                installation_url=candidate_url,
                canonical_installation_url=canonical_url,
                record_id=existing["record_id"] if existing else None,
                record_status="failed",
                content_hash=None,
                export_batch_id=export_batch_id,
            )

    next_unprocessed = state_store.next_unprocessed_position(
        source_lookup.source.source_id,
        inspection.candidate_installation_links,
    )
    state_store.update_export_batch_counts(
        export_batch_id=export_batch_id,
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        status="completed" if succeeded >= requested_count else "partial",
    )
    return BatchResult(
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        duplicates=duplicates,
        next_unprocessed_record=next_unprocessed,
        completed_urls=completed_urls,
        failed_urls=failed_urls,
        skipped_urls=skipped_urls,
        manifest_changes=manifest_changes,
        attempt_ceiling=attempt_ceiling,
    )


def parse_candidate(
    candidate_url: str,
    source_position: int,
    inspection: PageInspection,
    source_result: FetchResult,
    fetcher: BoundedFetcher,
    preview_run_id: str,
) -> ParsePreview:
    if is_same_page_fragment_candidate(candidate_url, inspection.normalized_url):
        return parse_inline_archive_record(
            source_result,
            source_archive_url=inspection.normalized_url,
            source_position=source_position,
            scrape_run_id=preview_run_id,
        )
    detail_result = fetcher.fetch(candidate_url, allowed_urls={candidate_url})
    return parse_installation_record(
        detail_result,
        source_archive_url=inspection.normalized_url,
        source_position=source_position,
        scrape_run_id=preview_run_id,
    )


def is_same_page_fragment_candidate(candidate_url: str, normalized_source_url: str) -> bool:
    if "#" not in candidate_url:
        return False
    base_url = candidate_url.split("#", 1)[0]
    return base_url == normalized_source_url


def is_valid_preview(parse_preview: ParsePreview) -> bool:
    return bool(parse_preview.record.title and not parse_preview.record.parsing_errors)


def compare_manifest(previous_records: dict[str, dict[str, object]], current_urls: list[str]) -> ManifestChangeReport:
    previous_index = {
        url: int(record.get("source_position", index + 1)) - 1
        for index, (url, record) in enumerate(previous_records.items())
    }
    current_index = {canonical_candidate_url(url): index for index, url in enumerate(current_urls)}
    previous_set = set(previous_index)
    current_set = set(current_index)
    unchanged: list[str] = []
    reordered: list[str] = []
    for url in previous_set & current_set:
        if previous_index[url] == current_index[url]:
            unchanged.append(url)
        else:
            reordered.append(url)
    return ManifestChangeReport(
        new_links=sorted(current_set - previous_set),
        removed_links=sorted(previous_set - current_set),
        reordered_links=sorted(reordered),
        unchanged_links=sorted(unchanged),
    )


def canonical_candidate_url(candidate_url: str) -> str:
    base_url, fragment = urldefrag(candidate_url)
    normalized = normalize_url(base_url)
    return f"{normalized}#{fragment}" if fragment else normalized


def content_hash_for(parse_preview: ParsePreview, source_result: FetchResult) -> str:
    return parse_preview.record.record_id or source_result.response_hash
