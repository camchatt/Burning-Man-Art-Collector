from __future__ import annotations

from pathlib import Path

from burning_man_scraper.exporters.artelier_scraper_csv_v1.map_row import clean_cell


# Collector BM-upload fields and how they relate to scraper v1.
COMPATIBLE_DIRECT = {
    "project_title": "project_title",
    "project_type": "project_type",
    "project_year": "year",
    "project_summary": "proof_excerpt (fallback)",
    "project_tags": "tags",
    "project_materials": "materials",
    "project_fabrication_methods": "fabrication_methods",
    "project_context_tags": "context_tags",
    "client_name": "client_or_commissioner",
    "hero_image_url": "image_urls",
    "contributor_website": "artist_website",
    "role_title": "contributor_role",
    "collaboration_status": "collaboration_status",
    "what_they_did": "what_they_did",
    "why_it_mattered": "why_it_matters",
    "proof_title": "proof_title",
    "proof_external_url": "proof_external_url",
    "proof_description": "proof_excerpt",
    "permission_status": "permission_status (normalized)",
    "bm_uid": "bm_uid + source_record_id",
    "bm_year": "bm_year + year",
    "bm_event_name": "bm_event_name",
    "playa_address": "bm_playa_address (+ location fallback)",
    "theme_camp": "bm_theme_camp",
    "installation_type": "bm_installation_type",
    "source_artist_credit": "bm_artist_text_raw",
    "contributor_kind": "contributor_kind (remapped)",
    "playa_name": "artist_alias",
    "contributor_display_name": "artist_name / organization_name",
}

REQUIRES_MAPPING = {
    "contributor_name": "Mapped into artist_name / organization_name via display rules",
    "contributor_slug": "Not emitted (Artelier derives slugs on import)",
    "project_slug": "Not emitted (Artelier derives slugs on import)",
    "project_visibility": "Not in scraper v1; Artelier staging defaults apply",
    "contributor_visibility": "Not in scraper v1",
    "contribution_visibility": "Not in scraper v1",
    "proof_visibility": "Not in scraper v1",
    "verification_status": "Not mapped; use review_status / import_notes",
    "approval_status": "Not mapped",
    "phase": "Not mapped",
    "contribution_category": "Not mapped",
    "contribution_title": "Not mapped",
    "public_credit_language": "Not mapped (partially covered by import_notes)",
    "contributor_email": "Not mapped (PII avoided in scraper contract)",
    "contributor_first_name": "Not mapped — do not invent splits into Artelier person fields",
    "contributor_last_name": "Not mapped — do not invent splits into Artelier person fields",
    "playa_name_confidence": "Retained only via import_notes / flags when uncertain",
    "honorarium_status": "bm_honorarium_status",
    "playa_latitude": "bm_latitude",
    "playa_longitude": "bm_longitude",
    "review_flags": "bm_review_flags + review_status / import_notes",
    "source_provenance": "bm_source_provenance",
    "additional_contributor_credits": "collaborators + bm_additional_credits",
    "bm_hero_image_source_url": "bm_hero_image_source_url (+ image_urls)",
    "hero_image_source_page": "source_record_url fallback + bm_hero_image_source_page",
    "hero_image_attribution": "Not currently mapped",
    "hero_image_confidence": "Influences proof/classification confidence indirectly",
    "project_classification_confidence": "classification_confidence",
}

BM_METADATA_SHOULD_REMAIN = [
    "bm_year",
    "bm_uid",
    "bm_event_name",
    "bm_playa_address",
    "bm_latitude",
    "bm_longitude",
    "bm_honorarium_status",
    "bm_theme_camp",
    "bm_installation_type",
    "bm_artist_text_raw",
    "bm_additional_credits",
    "bm_review_flags",
    "bm_source_provenance",
]

MAY_BE_LOST = [
    "project_slug",
    "contributor_slug",
    "contributor_email",
    "contributor_first_name",
    "contributor_last_name",
    "project_visibility / contributor_visibility / proof_visibility / contribution_visibility",
    "verification_status / approval_status / phase",
    "contribution_category / contribution_title / public_credit_language",
    "hero_image_attribution",
]


def compare_bm_upload_to_v1(
    source_rows: list[dict[str, str]],
    accepted: list[dict[str, str]],
    rejected: list[dict[str, str]],
) -> dict:
    needs_review = sum(
        1
        for row in accepted
        if clean_cell(row.get("review_status")) in {"Needs review", "Needs better source", "Duplicate"}
        or clean_cell(row.get("contributor_kind")) in {"collective", "unknown"}
    )
    return {
        "source_row_count": len(source_rows),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "fields_already_compatible": COMPATIBLE_DIRECT,
        "fields_requiring_mapping": REQUIRES_MAPPING,
        "fields_that_would_be_lost": MAY_BE_LOST,
        "fields_that_should_remain_bm_metadata": BM_METADATA_SHOULD_REMAIN,
        "rows_failing_validation": len(rejected),
        "rows_requiring_human_review": needs_review,
    }


def write_compatibility_report(path: Path, *, year: int, report: dict) -> None:
    lines = [
        f"# Compatibility report — BM upload {year} → artelier_scraper_csv_v1",
        "",
        f"- Source rows: {report['source_row_count']}",
        f"- Accepted v1 rows: {report['accepted_count']}",
        f"- Failed validation: {report['rows_failing_validation']}",
        f"- Likely human-review rows: {report['rows_requiring_human_review']}",
        "",
        "## Fields already compatible (direct or near-direct)",
        "",
    ]
    for src, dest in report["fields_already_compatible"].items():
        lines.append(f"- `{src}` → `{dest}`")
    lines.extend(["", "## Fields requiring mapping", ""])
    for src, note in report["fields_requiring_mapping"].items():
        lines.append(f"- `{src}` — {note}")
    lines.extend(["", "## Fields that would be lost (by design)", ""])
    for item in report["fields_that_would_be_lost"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Fields that should remain `bm_` metadata", ""])
    for item in report["fields_that_should_remain_bm_metadata"]:
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
            "## Validation / review expectations",
            "",
            f"- Rows that fail v1 validation: {report['rows_failing_validation']}",
            f"- Rows that should stay in Artelier staging review: {report['rows_requiring_human_review']}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
