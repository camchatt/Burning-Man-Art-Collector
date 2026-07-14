from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from burning_man_scraper.verification.models import VERIFICATION_SCHEMA_VERSION, ArchiveIndexRecord, VerificationResult
from burning_man_scraper.verification.processor import serialize_image_assets


REPORT_COLUMNS = [
    "year",
    "project_title",
    "verification_status",
    "archive_uid",
    "archive_url",
    "www_title",
    "www_uid",
    "legacy_link_status",
    "title_match_score",
    "artist_match_score",
    "description_match_score",
    "uid_match",
    "archive_artist",
    "export_artist",
    "image_count",
    "active_image_count",
    "hero_image_url",
    "hero_image_active",
    "public_credit_language",
    "warnings",
    "source",
]


def write_verification_report(
    output_dir: Path,
    *,
    year: int,
    results: list[VerificationResult],
    archive_records: list[ArchiveIndexRecord],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"verification_report_{year}.csv"
    json_path = output_dir / f"verification_report_{year}.json"
    manifest_path = output_dir / f"image_manifest_{year}.json"
    index_path = output_dir / f"archive_index_{year}.json"
    summary_path = output_dir / f"verification_summary_{year}.json"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for result in results:
            writer.writerow(result.to_row())

    json_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFICATION_SCHEMA_VERSION,
                "year": year,
                "results": [_result_to_json(result) for result in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFICATION_SCHEMA_VERSION,
                "year": year,
                "images": [
                    {
                        "project_title": result.project_title,
                        "archive_uid": result.archive_uid,
                        "archive_url": result.archive_url,
                        "images": serialize_image_assets(result.images),
                    }
                    for result in results
                    if result.images
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    index_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFICATION_SCHEMA_VERSION,
                "year": year,
                "record_count": len(archive_records),
                "records": [asdict(record) for record in archive_records],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = _build_summary(year, results, archive_records)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {
        "csv": csv_path,
        "json": json_path,
        "manifest": manifest_path,
        "index": index_path,
        "summary": summary_path,
    }


def _result_to_json(result: VerificationResult) -> dict:
    payload = result.to_row()
    payload["images"] = serialize_image_assets(result.images)
    return payload


def _build_summary(
    year: int,
    results: list[VerificationResult],
    archive_records: list[ArchiveIndexRecord],
) -> dict:
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.verification_status] = status_counts.get(result.verification_status, 0) + 1

    total_images = sum(result.image_count for result in results)
    active_images = sum(result.active_image_count for result in results)

    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "year": year,
        "archive_record_count": len(archive_records),
        "verified_project_count": len(results),
        "status_counts": status_counts,
        "total_images_checked": total_images,
        "active_images": active_images,
        "inactive_images": total_images - active_images,
    }
