from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from burning_man_scraper.enrichment.models import BatchRecord, ScrapeBatch
from burning_man_scraper.state import ScraperState


def list_available_batches(export_root: Path) -> list[ScrapeBatch]:
    batches_root = Path(export_root) / "burning_man"
    if not batches_root.exists():
        return []

    batches: list[ScrapeBatch] = []
    for batch_dir in batches_root.glob("*/batches/batch_*"):
        if not batch_dir.is_dir():
            continue
        manifest = load_json_file(batch_dir / "batch_manifest.json")
        year = batch_dir.parents[1].name
        batch_id = as_int(manifest.get("batch_id")) if manifest else None
        count = batch_record_count(batch_dir, manifest)
        batches.append(
            ScrapeBatch(
                export_batch_id=batch_id,
                year=year,
                batch_name=batch_dir.name,
                batch_directory=batch_dir,
                record_count=count,
            )
        )
    return sorted(batches, key=lambda batch: (batch.year, batch.batch_name, str(batch.batch_directory)))


def load_batch_records(
    batch: ScrapeBatch,
    state_store: ScraperState | None = None,
) -> list[BatchRecord]:
    full_export = batch.batch_directory / "full_export.json"
    if full_export.exists():
        records = records_from_full_export(full_export)
        if records:
            return records

    if state_store is not None and batch.export_batch_id is not None:
        records = records_from_sqlite(state_store, batch.export_batch_id)
        if records:
            return records

    artelier_csv = batch.batch_directory / "artelier_import.csv"
    if artelier_csv.exists():
        return records_from_csv(artelier_csv)
    return []


def records_from_full_export(path: Path) -> list[BatchRecord]:
    payload = load_json_file(path)
    if not isinstance(payload, list):
        return []

    records: list[BatchRecord] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        status = item.get("record_status")
        if status and status != "completed":
            continue
        original = item.get("original_scraped_values") if isinstance(item.get("original_scraped_values"), dict) else {}
        mapped = item.get("mapped_artelier_values") if isinstance(item.get("mapped_artelier_values"), dict) else {}
        source_urls = item.get("source_urls") if isinstance(item.get("source_urls"), dict) else {}
        record_id = first_text(
            original.get("record_id"),
            mapped.get("project_slug"),
            source_urls.get("canonical_installation_url"),
            source_urls.get("installation_url"),
            f"batch-record-{index}",
        )
        title = first_text(original.get("title"), mapped.get("project_title"), f"Record {index}")
        contributor = first_text(original.get("artist_display_text"), mapped.get("contributor_name"))
        records.append(
            BatchRecord(
                batch_index=len(records) + 1,
                project_record_id=record_id,
                project_title=title,
                contributor_name=contributor,
                source_position=as_int(item.get("source_position")),
                year=first_text(original.get("year"), mapped.get("project_year")) or None,
                source_url=first_text(
                    source_urls.get("canonical_installation_url"),
                    source_urls.get("installation_url"),
                    mapped.get("proof_external_url"),
                )
                or None,
                artist_collective=first_text(original.get("artist_collective")) or None,
                materials=first_text(original.get("materials"), mapped.get("project_materials")) or None,
                project_website=first_text(original.get("project_url"), mapped.get("proof_external_url")) or None,
                artist_website=first_text(original.get("website_url"), mapped.get("contributor_website")) or None,
                original_values=original,
                artelier_row=mapped,
            )
        )
    return records


def records_from_sqlite(state_store: ScraperState, export_batch_id: int) -> list[BatchRecord]:
    records: list[BatchRecord] = []
    for index, row in enumerate(state_store.records_for_export_batch(export_batch_id), start=1):
        if row.get("record_status") != "completed":
            continue
        original = parse_json_object(row.get("record_json")) or {}
        mapped = parse_json_object(row.get("artelier_row_json")) or {}
        record_id = first_text(
            original.get("record_id"),
            row.get("record_id"),
            mapped.get("project_slug"),
            row.get("canonical_installation_url"),
            f"batch-record-{index}",
        )
        title = first_text(original.get("title"), mapped.get("project_title"), f"Record {index}")
        contributor = first_text(original.get("artist_display_text"), mapped.get("contributor_name"))
        records.append(
            BatchRecord(
                batch_index=len(records) + 1,
                project_record_id=record_id,
                project_title=title,
                contributor_name=contributor,
                source_position=as_int(row.get("source_position")),
                year=first_text(original.get("year"), mapped.get("project_year")) or None,
                source_url=first_text(row.get("canonical_installation_url"), row.get("installation_url")) or None,
                artist_collective=first_text(original.get("artist_collective")) or None,
                materials=first_text(original.get("materials"), mapped.get("project_materials")) or None,
                project_website=first_text(original.get("project_url"), mapped.get("proof_external_url")) or None,
                artist_website=first_text(original.get("website_url"), mapped.get("contributor_website")) or None,
                original_values=original,
                artelier_row=mapped,
            )
        )
    return records


def records_from_csv(path: Path) -> list[BatchRecord]:
    records: list[BatchRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            record_id = first_text(row.get("proof_external_url"), row.get("project_slug"), f"csv-record-{index}")
            title = first_text(row.get("project_title"), f"Record {index}")
            contributor = first_text(row.get("contributor_name"))
            records.append(
                BatchRecord(
                    batch_index=len(records) + 1,
                    project_record_id=record_id,
                    project_title=title,
                    contributor_name=contributor,
                    year=first_text(row.get("project_year")) or None,
                    source_url=first_text(row.get("proof_external_url")) or None,
                    materials=first_text(row.get("project_materials")) or None,
                    project_website=first_text(row.get("proof_external_url")) or None,
                    artist_website=first_text(row.get("contributor_website")) or None,
                    original_values={},
                    artelier_row=dict(row),
                )
            )
    return records


def batch_record_count(batch_dir: Path, manifest: dict[str, Any] | None) -> int:
    if manifest:
        for key in ("successful_count", "requested_count"):
            value = as_int(manifest.get(key))
            if value is not None:
                return value
    records = records_from_full_export(batch_dir / "full_export.json")
    if records:
        return len(records)
    csv_path = batch_dir / "artelier_import.csv"
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _row in csv.DictReader(handle))
    return 0


def load_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def parse_json_object(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not value:
        return None
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else None


def first_text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def as_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
