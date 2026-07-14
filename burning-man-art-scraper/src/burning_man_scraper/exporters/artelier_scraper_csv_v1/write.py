from __future__ import annotations

import csv
import json
import time
from collections import Counter
from pathlib import Path

from burning_man_scraper.exporters.artelier_scraper_csv_v1.compatibility import (
    compare_bm_upload_to_v1,
    write_compatibility_report,
)
from burning_man_scraper.exporters.artelier_scraper_csv_v1.contract import (
    ARTELIER_SCRAPER_CSV_V1,
    EXPORT_COLUMNS,
    STANDARD_COLUMNS,
    load_contract_manifest,
    schema_sha256,
    verify_contract_checksum,
)
from burning_man_scraper.exporters.artelier_scraper_csv_v1.map_row import (
    clean_cell,
    http_url,
    map_bm_upload_row,
)
from burning_man_scraper.exporters.artelier_scraper_csv_v1.validate import (
    validate_export_row,
    validate_header,
)


FIELD_MAPPING: list[dict[str, str]] = [
    {"artelier": "contract_version", "collector": "(constant)", "notes": ARTELIER_SCRAPER_CSV_V1},
    {"artelier": "source_name", "collector": "(constant)", "notes": "Burning Man Art Archive"},
    {"artelier": "source_namespace", "collector": "(constant)", "notes": "burning_man"},
    {"artelier": "source_record_id", "collector": "bm_uid / project_slug", "notes": ""},
    {"artelier": "source_record_url", "collector": "proof_external_url | hero_image_source_page", "notes": ""},
    {"artelier": "contributor_kind", "collector": "contributor_kind", "notes": "individual→person; studio/theme_camp→organization"},
    {"artelier": "artist_name", "collector": "contributor_display_name / contributor_name", "notes": "person/collective/unknown"},
    {"artelier": "artist_alias", "collector": "playa_name", "notes": "Burner / alternate name"},
    {"artelier": "artist_website", "collector": "contributor_website", "notes": "http(s) only"},
    {"artelier": "organization_name", "collector": "contributor_display_name", "notes": "organization/collective"},
    {"artelier": "project_title", "collector": "project_title", "notes": ""},
    {"artelier": "proof_title", "collector": "proof_title", "notes": ""},
    {"artelier": "proof_external_url", "collector": "proof_external_url", "notes": ""},
    {"artelier": "proof_excerpt", "collector": "proof_description / project_summary", "notes": ""},
    {"artelier": "image_urls", "collector": "hero_image_url", "notes": "pipe-delimited"},
    {"artelier": "why_it_matters", "collector": "why_it_mattered", "notes": "rename"},
    {"artelier": "year", "collector": "project_year / bm_year", "notes": "YYYY"},
    {"artelier": "bm_*", "collector": "BM extensions", "notes": "namespaced metadata after standard columns"},
]


def default_source_csv(project_root: Path, year: int) -> Path:
    return project_root / "data" / "bm_ingest" / str(year) / f"artelier_bm_upload_{year}.csv"


def load_bm_upload_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def export_year_to_scraper_v1(
    project_root: Path,
    *,
    year: int,
    source_csv: Path | None = None,
    output_root: Path | None = None,
    run_id: str | None = None,
) -> dict:
    """Map collector BM upload CSV → artelier_scraper_csv_v1 run directory (does not mutate source)."""
    verify_contract_checksum(project_root)
    source = source_csv or default_source_csv(project_root, year)
    if not source.exists():
        raise FileNotFoundError(
            f"Missing collector upload CSV for {year}: {source}. "
            "Run prepare/ingest first (`py run_bm_ingest.py --year {year}`)."
        )

    stamp = run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = output_root or (
        project_root / "data" / "exports" / "artelier_scraper_csv_v1" / str(year) / stamp
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    source_rows = load_bm_upload_rows(source)
    mapped: list[dict[str, str]] = []
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    row_reports: list[dict] = []

    for index, src in enumerate(source_rows, start=2):
        row = map_bm_upload_row(src)
        errors, warnings = validate_export_row(row)
        report = {
            "source_row": index,
            "project_title": row.get("project_title"),
            "bm_uid": row.get("bm_uid"),
            "contributor_kind": row.get("contributor_kind"),
            "errors": errors,
            "warnings": warnings,
        }
        row_reports.append(report)
        mapped.append(row)
        if errors:
            rejected.append({**row, "validation_errors": " | ".join(errors)})
        else:
            accepted.append(row)

    header_errors = validate_header(EXPORT_COLUMNS)
    if header_errors:
        raise ValueError("; ".join(header_errors))

    upload_path = out_dir / f"artelier_scraper_csv_v1_{year}.csv"
    rejected_path = out_dir / f"artelier_scraper_csv_v1_{year}_rejected.csv"
    summary_path = out_dir / f"validation_summary_{year}.json"
    report_path = out_dir / f"validation_report_{year}.md"
    mapping_path = out_dir / f"field_mapping_{year}.md"
    compat_path = out_dir / f"compatibility_vs_bm_upload_{year}.md"

    _write_csv(upload_path, accepted, EXPORT_COLUMNS)
    rejected_fields = EXPORT_COLUMNS + ("validation_errors",)
    _write_csv(rejected_path, rejected, rejected_fields)

    kind_counts = Counter(clean_cell(row.get("contributor_kind")) or "unknown" for row in accepted)
    review_counts = Counter(clean_cell(row.get("review_status")) or "(blank)" for row in accepted)
    summary = {
        "contract": ARTELIER_SCRAPER_CSV_V1,
        "schema_sha256": schema_sha256(project_root),
        "manifest": load_contract_manifest(project_root),
        "year": year,
        "source_csv": str(source),
        "output_dir": str(out_dir),
        "source_row_count": len(source_rows),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "contributor_kind_counts": dict(kind_counts),
        "review_status_counts": dict(review_counts),
        "missing_artist_name": sum(1 for row in accepted if not clean_cell(row.get("artist_name"))),
        "missing_project_title": sum(1 for row in accepted if not clean_cell(row.get("project_title"))),
        "missing_proof_url": sum(1 for row in accepted if not http_url(row.get("proof_external_url"))),
        "missing_image_urls": sum(1 for row in accepted if not clean_cell(row.get("image_urls"))),
        "standard_column_count": len(STANDARD_COLUMNS),
        "export_column_count": len(EXPORT_COLUMNS),
        "header": list(EXPORT_COLUMNS),
        "output_files": {
            "upload": str(upload_path),
            "rejected": str(rejected_path),
            "validation_summary": str(summary_path),
            "validation_report": str(report_path),
            "field_mapping": str(mapping_path),
            "compatibility_report": str(compat_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    report_lines = [
        f"# Artelier scraper CSV v1 validation — {year}",
        "",
        f"- Source: `{source}`",
        f"- Accepted: {len(accepted)}",
        f"- Rejected: {len(rejected)}",
        f"- Contributor kinds: {dict(kind_counts)}",
        f"- Review statuses: {dict(review_counts)}",
        "",
        "## Rejected / blocked rows",
        "",
    ]
    if not rejected:
        report_lines.append("None.")
    else:
        for item in row_reports:
            if not item["errors"]:
                continue
            report_lines.append(
                f"- row {item['source_row']} `{item.get('project_title') or '(untitled)'}` "
                f"({item.get('bm_uid') or 'no uid'}): {'; '.join(item['errors'])}"
            )
    report_lines.extend(["", "## Warnings (accepted rows)", ""])
    warned = [item for item in row_reports if item["warnings"] and not item["errors"]]
    if not warned:
        report_lines.append("None.")
    else:
        for item in warned[:100]:
            report_lines.append(
                f"- row {item['source_row']} `{item.get('project_title')}`: {'; '.join(item['warnings'])}"
            )
        if len(warned) > 100:
            report_lines.append(f"- … {len(warned) - 100} more")
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    mapping_lines = [
        f"# Field mapping — collector BM upload → {ARTELIER_SCRAPER_CSV_V1}",
        "",
        "| Artelier column | Collector source | Notes |",
        "|---|---|---|",
    ]
    for item in FIELD_MAPPING:
        mapping_lines.append(f"| `{item['artelier']}` | `{item['collector']}` | {item['notes']} |")
    mapping_path.write_text("\n".join(mapping_lines) + "\n", encoding="utf-8")

    compat = compare_bm_upload_to_v1(source_rows, accepted, rejected)
    write_compatibility_report(compat_path, year=year, report=compat)
    summary["compatibility"] = compat

    return summary
