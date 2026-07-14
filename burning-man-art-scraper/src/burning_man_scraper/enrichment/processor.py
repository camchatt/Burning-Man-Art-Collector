from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import json
from pathlib import Path

from burning_man_scraper.artelier_schema import ImportSchema, format_row_for_schema
from burning_man_scraper.enrichment.cache import SearchCache
from burning_man_scraper.enrichment.models import BatchRecord, EnrichmentRun
from burning_man_scraper.enrichment.research import FetchClient, build_enrichment_preview
from burning_man_scraper.enrichment.providers import SearchProvider
from burning_man_scraper.enrichment.state import ENRICHMENT_SCHEMA_VERSION, EnrichmentState


@dataclass(frozen=True)
class EnrichmentBatchResult:
    enrichment_run_id: int
    requested_count: int
    attempted_count: int
    completed_count: int
    failed_count: int
    skipped_count: int
    no_sources_count: int
    approved_change_count: int
    rejected_change_count: int
    unresolved_change_count: int
    enriched_record_ids: list[str]
    next_unenriched_record: int | None
    enriched_csv: Path
    review_csv: Path
    manifest_json: Path


def process_approved_enrichment_batch(
    enrichment_state: EnrichmentState,
    run: EnrichmentRun,
    batch_records: list[BatchRecord],
    selected_records: list[BatchRecord],
    schema: ImportSchema,
    search_client: SearchProvider,
    fetch_client: FetchClient,
    search_cache: SearchCache | None = None,
    approval_mode: str = "manual_review_required",
    confidence_threshold: float = 0.85,
) -> EnrichmentBatchResult:
    attempted = 0
    completed = 0
    failed = 0
    skipped = 0
    no_sources = 0
    enriched_record_ids: list[str] = []

    for record in selected_records:
        attempted += 1
        try:
            preview = build_enrichment_preview(
                record=record,
                schema=schema,
                search_client=search_client,
                fetch_client=fetch_client,
                search_cache=search_cache,
            )
            enrichment_state.save_proposed_changes(
                run.enrichment_run_id,
                record,
                preview.proposed_changes,
                approval_mode=approval_mode,
                confidence_threshold=confidence_threshold,
            )
            if preview.proposed_changes:
                status = "enriched" if any(change.confidence >= confidence_threshold for change in preview.proposed_changes) else "partially_enriched"
                enriched_record_ids.append(record.project_record_id)
            elif preview.sources:
                status = "sources_found"
            else:
                status = "no_credible_sources_found"
                no_sources += 1
            enrichment_state.update_record_result(
                run.enrichment_run_id,
                record.project_record_id,
                enrichment_status=status,
                search_status="searched",
                source_count=len(preview.sources),
                proposed_change_count=len(preview.proposed_changes),
            )
            completed += 1
        except Exception as exc:
            failed += 1
            enrichment_state.update_record_result(
                run.enrichment_run_id,
                record.project_record_id,
                enrichment_status="failed",
                search_status="failed",
                last_error=str(exc),
            )

    status = "completed" if failed == 0 else "partial"
    enrichment_state.update_run_counts(run.enrichment_run_id, completed, failed, skipped, status)
    paths = write_enrichment_outputs(
        enrichment_state=enrichment_state,
        run=run,
        batch_records=batch_records,
        schema=schema,
    )
    changes = enrichment_state.changes_for_batch(run.export_batch_id, run.source_batch_directory)
    approved_count = len([change for change in changes if change["review_status"] in {"approved", "edited"}])
    rejected_count = len([change for change in changes if change["review_status"] == "rejected"])
    unresolved_count = len([change for change in changes if change["review_status"] == "unresolved"])
    next_record = next_unenriched_position(enrichment_state, run, batch_records)
    manifest = build_enrichment_manifest(
        enrichment_state=enrichment_state,
        run=run,
        schema=schema,
        requested_count=run.requested_count,
        attempted_count=attempted,
        completed_count=completed,
        failed_count=failed,
        skipped_count=skipped,
        no_sources_count=no_sources,
        approved_change_count=approved_count,
        rejected_change_count=rejected_count,
        unresolved_change_count=unresolved_count,
        enriched_record_ids=enriched_record_ids,
        next_unenriched_record=next_record,
    )
    paths[2].write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return EnrichmentBatchResult(
        enrichment_run_id=run.enrichment_run_id,
        requested_count=run.requested_count,
        attempted_count=attempted,
        completed_count=completed,
        failed_count=failed,
        skipped_count=skipped,
        no_sources_count=no_sources,
        approved_change_count=approved_count,
        rejected_change_count=rejected_count,
        unresolved_change_count=unresolved_count,
        enriched_record_ids=enriched_record_ids,
        next_unenriched_record=next_record,
        enriched_csv=paths[0],
        review_csv=paths[1],
        manifest_json=paths[2],
    )


def write_enrichment_outputs(
    enrichment_state: EnrichmentState,
    run: EnrichmentRun,
    batch_records: list[BatchRecord],
    schema: ImportSchema,
) -> tuple[Path, Path, Path]:
    batch_dir = run.source_batch_directory
    enriched_csv = batch_dir / "enriched_artelier_import.csv"
    review_csv = batch_dir / "enrichment_review.csv"
    manifest_json = batch_dir / "enrichment_manifest.json"
    changes = enrichment_state.changes_for_batch(run.export_batch_id, batch_dir)
    rows = enriched_rows_from_changes(batch_records, changes, schema)
    write_artelier_rows(enriched_csv, rows, schema)
    write_review_rows(review_csv, changes)
    return enriched_csv, review_csv, manifest_json


def enriched_rows_from_changes(
    batch_records: list[BatchRecord],
    changes: list[dict[str, object]],
    schema: ImportSchema,
) -> list[dict[str, str]]:
    approved = {
        (str(change["project_record_id"]), str(change["artelier_field"])): str(change["final_value"] or "")
        for change in changes
        if change["review_status"] in {"approved", "edited"}
    }
    rows: list[dict[str, str]] = []
    for record in batch_records:
        row = {header: "" for header in schema.headers}
        for key, value in (record.artelier_row or {}).items():
            if key in row:
                row[key] = csv_value(value)
        for header in schema.headers:
            key = (record.project_record_id, header)
            if key in approved:
                row[header] = approved[key]
        rows.append(format_row_for_schema(row, schema))
    return rows


def write_artelier_rows(path: Path, rows: list[dict[str, str]], schema: ImportSchema) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=schema.headers, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def write_review_rows(path: Path, changes: list[dict[str, object]]) -> None:
    fieldnames = [
        "project_record_id",
        "project_title",
        "contributor_name",
        "artelier_field",
        "original_value",
        "proposed_value",
        "final_value",
        "evidence_classification",
        "confidence",
        "source_url",
        "source_title",
        "source_excerpt",
        "review_status",
        "review_notes",
        "enrichment_run_id",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for change in changes:
            writer.writerow({field: csv_value(change.get(field)) for field in fieldnames})


def build_enrichment_manifest(
    enrichment_state: EnrichmentState,
    run: EnrichmentRun,
    schema: ImportSchema,
    requested_count: int,
    attempted_count: int,
    completed_count: int,
    failed_count: int,
    skipped_count: int,
    no_sources_count: int,
    approved_change_count: int,
    rejected_change_count: int,
    unresolved_change_count: int,
    enriched_record_ids: list[str],
    next_unenriched_record: int | None,
) -> dict[str, object]:
    run_row = enrichment_state.run_row(run.enrichment_run_id)
    return {
        "enrichment_run_id": run.enrichment_run_id,
        "export_batch_id": run.export_batch_id,
        "source_batch_directory": str(run.source_batch_directory),
        "enrichment_schema_version": ENRICHMENT_SCHEMA_VERSION,
        "artelier_schema_version": schema.schema_version,
        "requested_count": requested_count,
        "attempted_count": attempted_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "no_sources_count": no_sources_count,
        "approved_change_count": approved_change_count,
        "rejected_change_count": rejected_change_count,
        "unresolved_change_count": unresolved_change_count,
        "enriched_record_ids": enriched_record_ids,
        "started_at": run_row["started_at"],
        "completed_at": utc_now(),
        "enriched_artelier_import_filename": "enriched_artelier_import.csv",
        "enrichment_review_filename": "enrichment_review.csv",
        "next_unenriched_record": next_unenriched_record,
    }


def next_unenriched_position(
    enrichment_state: EnrichmentState,
    run: EnrichmentRun,
    batch_records: list[BatchRecord],
) -> int | None:
    statuses = enrichment_state.latest_status_by_project(run.export_batch_id, run.source_batch_directory)
    for record in batch_records:
        if record.project_record_id not in statuses:
            return record.batch_index
    return None


def csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "||".join(str(item) for item in value)
    return str(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

