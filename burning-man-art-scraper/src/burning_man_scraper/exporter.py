from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import re

from burning_man_scraper.artelier_schema import ImportSchema
from burning_man_scraper.batch import BatchResult
from burning_man_scraper.state import ScraperState, Source


@dataclass(frozen=True)
class ExportPaths:
    batch_directory: Path
    artelier_csv: Path
    full_json: Path
    batch_manifest: Path
    export_history: Path
    consolidated_csv: Path
    consolidated_json: Path


def export_completed_batch(
    state_store: ScraperState,
    source: Source,
    export_batch_id: int,
    batch_result: BatchResult,
    requested_count: int,
    schema: ImportSchema,
    export_root: Path,
    overwrite_batch: int | None = None,
    overwrite_confirmed: bool = False,
) -> ExportPaths:
    year_or_slug = source.detected_year or source_slug(source.normalized_url)
    base_dir = export_root / "burning_man" / year_or_slug
    batch_dir = next_batch_directory(
        base_dir / "batches",
        overwrite_batch=overwrite_batch,
        overwrite_confirmed=overwrite_confirmed,
    )
    batch_dir.mkdir(parents=True, exist_ok=True)

    records = state_store.records_for_export_batch(export_batch_id)
    successful_records = [record for record in records if record["record_status"] == "completed"]
    artelier_rows = [load_json_object(record.get("artelier_row_json")) for record in successful_records]
    artelier_rows = [row for row in artelier_rows if row]
    write_artelier_csv(batch_dir / "artelier_import.csv", artelier_rows, schema)
    write_full_json(batch_dir / "full_export.json", records)

    manifest = build_batch_manifest(
        source=source,
        export_batch_id=export_batch_id,
        batch_result=batch_result,
        requested_count=requested_count,
        records=records,
        schema=schema,
    )
    (batch_dir / "batch_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    history_path = export_root / "burning_man" / "export_history.csv"
    append_export_history(history_path, manifest, year_or_slug, batch_dir)
    write_consolidated_exports(
        state_store=state_store,
        source=source,
        schema=schema,
        consolidated_dir=base_dir / "consolidated",
    )
    state_store.update_export_batch_file(export_batch_id, str(batch_dir / "artelier_import.csv"))
    return ExportPaths(
        batch_directory=batch_dir,
        artelier_csv=batch_dir / "artelier_import.csv",
        full_json=batch_dir / "full_export.json",
        batch_manifest=batch_dir / "batch_manifest.json",
        export_history=history_path,
        consolidated_csv=base_dir / "consolidated" / f"burning_man_{year_or_slug}_all_completed.csv",
        consolidated_json=base_dir / "consolidated" / f"burning_man_{year_or_slug}_all_completed.json",
    )


def next_batch_directory(
    batches_dir: Path,
    overwrite_batch: int | None = None,
    overwrite_confirmed: bool = False,
) -> Path:
    batches_dir.mkdir(parents=True, exist_ok=True)
    if overwrite_batch is not None:
        target = batches_dir / f"batch_{overwrite_batch:03d}"
        if target.exists() and not overwrite_confirmed:
            files = sorted(path.name for path in target.iterdir())
            raise ValueError(f"Overwrite requires confirmation. Files that would be replaced: {files}")
        return target

    existing_numbers = []
    for path in batches_dir.glob("batch_*"):
        match = re.fullmatch(r"batch_(\d{3})", path.name)
        if match and path.is_dir():
            existing_numbers.append(int(match.group(1)))
    next_number = max(existing_numbers, default=0) + 1
    return batches_dir / f"batch_{next_number:03d}"


def write_artelier_csv(path: Path, rows: list[dict[str, object]], schema: ImportSchema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=schema.headers, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: csv_value(row.get(header)) for header in schema.headers})


def write_full_json(path: Path, records: list[dict[str, object]]) -> None:
    payload = []
    for record in records:
        payload.append(
            {
                "mapped_artelier_values": load_json_object(record.get("artelier_row_json")),
                "original_scraped_values": load_json_object(record.get("record_json")),
                "source_urls": {
                    "installation_url": record.get("installation_url"),
                    "canonical_installation_url": record.get("canonical_installation_url"),
                },
                "warnings": (load_json_object(record.get("record_json")) or {}).get("warnings"),
                "extraction_errors": (load_json_object(record.get("record_json")) or {}).get("parsing_errors"),
                "source_position": record.get("source_position"),
                "record_status": record.get("record_status"),
            }
        )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def build_batch_manifest(
    source: Source,
    export_batch_id: int,
    batch_result: BatchResult,
    requested_count: int,
    records: list[dict[str, object]],
    schema: ImportSchema,
) -> dict[str, object]:
    positions = [int(record["source_position"]) for record in records if record.get("source_position")]
    return {
        "batch_id": export_batch_id,
        "normalized_source_url": source.normalized_url,
        "detected_year": source.detected_year,
        "requested_count": requested_count,
        "attempted_count": batch_result.attempted,
        "successful_count": batch_result.succeeded,
        "failed_count": batch_result.failed,
        "skipped_count": batch_result.skipped,
        "first_source_position": min(positions) if positions else None,
        "last_source_position": max(positions) if positions else None,
        "next_unprocessed_position": batch_result.next_unprocessed_record,
        "created_at": utc_now(),
        "artelier_csv_filename": "artelier_import.csv",
        "full_json_filename": "full_export.json",
        "schema_version": schema.schema_version,
    }


def append_export_history(
    history_path: Path,
    manifest: dict[str, object],
    year_or_slug: str,
    batch_dir: Path,
) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "batch_id",
        "year",
        "normalized_source_url",
        "requested_count",
        "successful_count",
        "failed_count",
        "skipped_count",
        "first_source_position",
        "last_source_position",
        "next_unprocessed_position",
        "export_directory",
        "completed_at",
    ]
    write_header = not history_path.exists()
    with history_path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "batch_id": manifest["batch_id"],
                "year": year_or_slug,
                "normalized_source_url": manifest["normalized_source_url"],
                "requested_count": manifest["requested_count"],
                "successful_count": manifest["successful_count"],
                "failed_count": manifest["failed_count"],
                "skipped_count": manifest["skipped_count"],
                "first_source_position": manifest["first_source_position"],
                "last_source_position": manifest["last_source_position"],
                "next_unprocessed_position": manifest["next_unprocessed_position"],
                "export_directory": str(batch_dir),
                "completed_at": utc_now(),
            }
        )


def write_consolidated_exports(
    state_store: ScraperState,
    source: Source,
    schema: ImportSchema,
    consolidated_dir: Path,
) -> None:
    consolidated_dir.mkdir(parents=True, exist_ok=True)
    year_or_slug = source.detected_year or source_slug(source.normalized_url)
    records = state_store.completed_records_for_source(source.source_id)
    rows = [load_json_object(record.get("artelier_row_json")) for record in records]
    rows = [row for row in rows if row]
    write_artelier_csv(consolidated_dir / f"burning_man_{year_or_slug}_all_completed.csv", rows, schema)
    payload = [
        {
            "mapped_artelier_values": load_json_object(record.get("artelier_row_json")),
            "original_scraped_values": load_json_object(record.get("record_json")),
            "source_position": record.get("source_position"),
            "record_status": record.get("record_status"),
        }
        for record in records
    ]
    (consolidated_dir / f"burning_man_{year_or_slug}_all_completed.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "||".join(str(item) for item in value)
    return str(value)


def load_json_object(value: object) -> dict[str, object] | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    return json.loads(str(value))


def source_slug(normalized_url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalized_url.lower()).strip("-")
    return slug[:80] or "unknown-source"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
