"""Burning Man / PlayaEvents CSV source adapter (compatibility wrapper)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from burning_man_scraper.bm_ingest.sources import cache_inventory
from burning_man_scraper.sources.artelier_map import artelier_headers
from burning_man_scraper.sources.base import (
    FieldValue,
    NormalizedRecord,
    SourceDescriptor,
    SourceInspectResult,
)
from burning_man_scraper.sources.run_store import (
    create_run,
    write_artelier_outputs,
    write_normalized_records,
)
from burning_man_scraper.verification.www_loader import (
    assert_playaevents_art_csv,
    resolve_art_csv_year,
)

DEFAULT_IDENTITY_LIMIT = 50


DESCRIPTOR = SourceDescriptor(
    id="burning_man_csv",
    label="Burning Man CSV",
    description="Upload a PlayaEvents ART CSV and match it to the History Archive.",
    input_kind="file",
    fields=[
        {"name": "file", "label": "PlayaEvents ART CSV", "required": True, "type": "file"},
        {
            "name": "run_identity_online",
            "label": "Search the web for unclear artist names",
            "required": False,
            "type": "boolean",
            "default": False,
        },
    ],
)


def _field(value: str, *, status: str = "sourced", confidence: str = "high") -> FieldValue:
    return FieldValue(
        value=value or "",
        status=status if value else "missing",  # type: ignore[arg-type]
        confidence=confidence if value else "none",
    )


def bm_row_to_normalized(row: dict[str, str], headers: list[str]) -> NormalizedRecord:
    artelier = {header: row.get(header, "") for header in headers}
    flags = [flag for flag in (row.get("review_flags") or "").split("|") if flag]
    uid = row.get("bm_uid") or row.get("project_slug") or ""
    return NormalizedRecord(
        record_id=f"bm_{uid}" if uid else f"bm_{artelier.get('project_slug') or 'row'}",
        source_id="burning_man_csv",
        source_record_id=uid,
        source_record_url=artelier.get("proof_external_url") or "",
        project_title=_field(artelier.get("project_title") or ""),
        contributor_name=_field(artelier.get("contributor_name") or row.get("contributor_display_name") or ""),
        project_year=_field(artelier.get("project_year") or row.get("bm_year") or ""),
        project_location=_field(artelier.get("project_location") or row.get("playa_address") or ""),
        project_type=_field(artelier.get("project_type") or ""),
        collection=_field(row.get("theme_camp") or ""),
        hero_image_url=_field(artelier.get("hero_image_url") or ""),
        proof_external_url=_field(artelier.get("proof_external_url") or ""),
        project_summary=_field(artelier.get("project_summary") or ""),
        project_tags=_field(artelier.get("project_tags") or ""),
        project_materials=_field(artelier.get("project_materials") or ""),
        project_fabrication_methods=_field(artelier.get("project_fabrication_methods") or ""),
        project_context_tags=_field(artelier.get("project_context_tags") or ""),
        collaboration_status=_field(artelier.get("collaboration_status") or ""),
        collaborators=_field(row.get("additional_contributor_credits") or ""),
        client_name=_field(artelier.get("client_name") or ""),
        approval_status=artelier.get("approval_status") or "draft",
        verification_status=artelier.get("verification_status") or "documented",
        permission_status=artelier.get("permission_status") or "pending_permission",
        review_flags=flags,
        relationships={
            "contributors": [artelier.get("contributor_name") or ""],
            "collections": [row.get("theme_camp") or ""] if row.get("theme_camp") else [],
            "styles_tags": [part for part in (artelier.get("project_tags") or "").split("|") if part],
        },
        raw_evidence={
            "bm_uid": row.get("bm_uid") or "",
            "playa_address": row.get("playa_address") or "",
            "source_provenance": row.get("source_provenance") or "",
        },
        artelier_row=artelier,
    )


class BurningManCsvAdapter:
    descriptor = DESCRIPTOR

    def inspect(self, *, art_path: Path, original_filename: str = "", project_root: Path | None = None, **_: Any) -> SourceInspectResult:
        assert_playaevents_art_csv(art_path)
        year = resolve_art_csv_year(art_path, original_filename=original_filename or art_path.name)
        with art_path.open("r", encoding="utf-8-sig", newline="") as handle:
            row_count = max(sum(1 for _ in handle) - 1, 0)
        inventory = cache_inventory(project_root, year) if project_root is not None else {}
        return SourceInspectResult(
            ok=True,
            source_id=self.descriptor.id,
            detected_label="Burning Man / PlayaEvents ART CSV",
            message=(
                f"{row_count} projects for Burning Man {year}. "
                "Upload is used for this run only; What When Where Files stay untouched."
            ),
            summary={
                "year": year,
                "rows": row_count,
                "filename": original_filename or art_path.name,
                "cache_inventory": inventory,
            },
        )

    def prepare(
        self,
        *,
        project_root: Path,
        art_path: Path,
        original_filename: str = "",
        confirm_overwrite: bool = False,
        run_identity_online: bool = False,
        identity_limit: int | None = DEFAULT_IDENTITY_LIMIT,
        also_write_run: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        from burning_man_scraper.aggregator_hub.prepare import run_prepare_pipeline

        result = run_prepare_pipeline(
            project_root=project_root,
            art_path=art_path,
            original_filename=original_filename,
            confirm_overwrite=confirm_overwrite,
            run_identity_online=run_identity_online,
            identity_limit=identity_limit,
        )
        if not result.get("ok"):
            return result

        year = int(result["year"])
        if also_write_run:
            upload_csv = project_root / "data" / "bm_ingest" / str(year) / f"artelier_bm_upload_{year}.csv"
            headers = artelier_headers(project_root)
            rows: list[dict[str, str]] = []
            if upload_csv.exists():
                with upload_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    rows = [dict(row) for row in reader]
            run_path = create_run(
                project_root,
                source_id=self.descriptor.id,
                label=f"Burning Man {year}",
                input_summary={"year": year, "filename": original_filename},
            )
            records = [bm_row_to_normalized(row, headers) for row in rows]
            write_normalized_records(run_path, records)
            artelier_rows = [dict(rec.artelier_row) for rec in records]
            for row, original in zip(artelier_rows, rows):
                row["review_flags"] = original.get("review_flags") or ""
            write_artelier_outputs(
                run_path,
                project_root,
                artelier_rows=artelier_rows,
                label=f"Burning Man {year}",
                unsupported_relationships=[
                    {
                        "note": "BM extension columns (playa, GIS, honorarium) remain in year bm_ingest CSV, not core-only export.",
                    }
                ],
            )
            result["run_id"] = run_path.name
            result["viewer_reload_run"] = f"/api/view?run_id={run_path.name}"
        result["source_id"] = self.descriptor.id
        return result
