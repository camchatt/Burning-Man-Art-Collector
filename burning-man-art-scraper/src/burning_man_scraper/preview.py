from __future__ import annotations

import json
from pathlib import Path
import csv

from burning_man_scraper.config import ScraperConfig
from burning_man_scraper.artelier_schema import ArtelierPreview
from burning_man_scraper.fetcher import FetchResult
from burning_man_scraper.inspection import PageInspection
from burning_man_scraper.models import InstallationRecord
from burning_man_scraper.record_parser import ParsePreview
from burning_man_scraper.state import hash_value


def configuration_hash(config: ScraperConfig) -> str:
    payload = {
        "max_records_per_run": config.max_records_per_run,
        "request_delay_seconds": config.request_delay_seconds,
        "request_timeout_seconds": config.request_timeout_seconds,
        "max_retries": config.max_retries,
        "user_agent": config.user_agent,
    }
    return hash_value(json.dumps(payload, sort_keys=True))


def write_raw_html(fetch_result: FetchResult, raw_html_dir: Path) -> tuple[Path, Path]:
    raw_html_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hash_value(fetch_result.requested_url)
    html_path = raw_html_dir / f"{url_hash}.html"
    metadata_path = raw_html_dir / f"{url_hash}.metadata.json"
    html_path.write_bytes(fetch_result.body)
    metadata = {
        "requested_url": fetch_result.requested_url,
        "final_url": fetch_result.final_url,
        "status_code": fetch_result.status_code,
        "fetched_timestamp": fetch_result.fetched_timestamp,
        "content_type": fetch_result.content_type,
        "response_hash": fetch_result.response_hash,
        "etag": fetch_result.etag,
        "last_modified": fetch_result.last_modified,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return html_path, metadata_path


def write_source_manifest(
    inspection: PageInspection,
    requested_count: int,
    config: ScraperConfig,
) -> Path:
    config.preview_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "supplied_source_url": inspection.entered_url,
        "normalized_source_url": inspection.normalized_url,
        "detected_page_type": inspection.detected_page_type,
        "detected_year": inspection.detected_year,
        "candidate_installation_count": len(inspection.candidate_installation_links),
        "ordered_candidate_installation_urls": inspection.candidate_installation_links,
        "excluded_urls": [link.url for link in inspection.excluded_links],
        "exclusion_reasons": [
            {"url": link.url, "reason": link.reason} for link in inspection.excluded_links
        ],
        "pagination_detected": inspection.pagination_detected,
        "pagination_authorized": False,
        "proposed_request_count": requested_count,
        "parser_version": inspection.parser_version,
        "configuration_hash": configuration_hash(config),
    }
    config.preview_manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return config.preview_manifest_path


def write_first_record_preview(parse_preview: ParsePreview, preview_root: Path) -> tuple[Path, Path, Path]:
    preview_root.mkdir(parents=True, exist_ok=True)
    payload = parse_preview.record.model_dump(mode="json")
    json_path = preview_root / "first_record_preview.json"
    csv_path = preview_root / "first_record_preview.csv"
    md_path = preview_root / "first_record_preview.md"

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(payload.keys()))
        writer.writeheader()
        writer.writerow({key: serialize_csv_value(value) for key, value in payload.items()})
    md_path.write_text(render_record_markdown(parse_preview), encoding="utf-8")
    return json_path, csv_path, md_path


def write_artelier_preview_files(
    artelier_preview: ArtelierPreview,
    parse_preview: ParsePreview,
    preview_root: Path,
) -> tuple[Path | None, Path, Path]:
    preview_root.mkdir(parents=True, exist_ok=True)
    import_path = preview_root / "artelier_import_preview.csv"
    research_path = preview_root / "full_research.json"
    review_path = preview_root / "review.csv"

    if artelier_preview.valid:
        with import_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=artelier_preview.schema.headers, extrasaction="raise")
            writer.writeheader()
            writer.writerow(artelier_preview.row)
    else:
        import_path = None

    research_payload = {
        "schema_version": parse_preview.record.schema_version,
        "artelier_schema_version": artelier_preview.schema.schema_version,
        "provenance": {
            "source_url": parse_preview.record.source_url,
            "canonical_source_url": parse_preview.record.canonical_source_url,
            "source_archive_url": parse_preview.record.source_archive_url,
            "source_accessed_at": parse_preview.record.source_accessed_at,
            "scrape_run_id": parse_preview.record.scrape_run_id,
            "source_position": parse_preview.source_position,
        },
        "warnings": parse_preview.record.warnings,
        "extraction_metadata": {
            "extraction_confidence": parse_preview.record.extraction_confidence,
            "missing_fields": parse_preview.record.missing_fields,
            "parsing_errors": parse_preview.record.parsing_errors,
            "needs_manual_review": parse_preview.record.needs_manual_review,
        },
        "parser_information": {
            "parser_version": parse_preview.record.parser_version,
            "schema_version": parse_preview.record.schema_version,
        },
        "record": parse_preview.record.model_dump(mode="json"),
        "artelier_preview": {
            "row": artelier_preview.row,
            "validations": [
                {
                    "field_name": validation.field_name,
                    "value": validation.value,
                    "valid": validation.valid,
                    "errors": validation.errors,
                }
                for validation in artelier_preview.validations
            ],
            "unmapped_source_fields": artelier_preview.unmapped_source_fields,
        },
    }
    research_path.write_text(json.dumps(research_payload, indent=2, sort_keys=True), encoding="utf-8")

    with review_path.open("w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "field_name",
            "mapped_value",
            "validation_result",
            "warnings",
            "confidence",
            "unmapped_values",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for validation in artelier_preview.validations:
            if validation.valid and validation.field_name not in {"project_title", "proof_external_url"}:
                continue
            writer.writerow(
                {
                    "field_name": validation.field_name,
                    "mapped_value": validation.value,
                    "validation_result": "valid" if validation.valid else "; ".join(validation.errors),
                    "warnings": "; ".join(parse_preview.record.warnings),
                    "confidence": parse_preview.record.extraction_confidence,
                    "unmapped_values": "; ".join(artelier_preview.unmapped_source_fields),
                }
            )
    return import_path, research_path, review_path


def render_record_markdown(parse_preview: ParsePreview) -> str:
    record = parse_preview.record
    lines = [
        "# First Record Preview",
        "",
        f"- Source position: {parse_preview.source_position}",
        f"- Schema version: {record.schema_version}",
        f"- Parser version: {record.parser_version}",
        "",
        "## Fields",
        "",
    ]
    for key, value in record.model_dump(mode="json").items():
        lines.append(f"- `{key}`: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            f"- Source URL: {record.source_url}",
            f"- Canonical source URL: {record.canonical_source_url}",
            f"- Source archive URL: {record.source_archive_url}",
            f"- Source accessed at: {record.source_accessed_at}",
            f"- Scrape run ID: {record.scrape_run_id}",
        ]
    )
    return "\n".join(lines) + "\n"


def serialize_csv_value(value: object) -> object:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def record_null_fields(record: InstallationRecord) -> list[str]:
    return [key for key, value in record.model_dump(mode="json").items() if value is None]


def record_empty_arrays(record: InstallationRecord) -> list[str]:
    return [key for key, value in record.model_dump(mode="json").items() if value == []]
