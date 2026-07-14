from __future__ import annotations

import re
from typing import Iterable

from burning_man_scraper.exporters.artelier_scraper_csv_v1.contract import (
    ARTELIER_SCRAPER_CSV_V1,
    CLASSIFICATION_SOURCE_VALUES,
    CONFIDENCE_VALUES,
    CONTRIBUTOR_KINDS,
    EXPORT_COLUMNS,
    PERMISSION_STATUS_VALUES,
    REVIEW_STATUS_VALUES,
    SOURCE_GRANULARITY_VALUES,
    STANDARD_COLUMNS,
)
from burning_man_scraper.exporters.artelier_scraper_csv_v1.map_row import clean_cell, http_url


def row_has_minimum_data(row: dict[str, str]) -> bool:
    return any(
        clean_cell(row.get(field))
        for field in ("project_title", "artist_name", "organization_name", "proof_external_url")
    )


def validate_export_row(row: dict[str, str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if clean_cell(row.get("contract_version")) != ARTELIER_SCRAPER_CSV_V1:
        errors.append(f'contract_version must be "{ARTELIER_SCRAPER_CSV_V1}"')

    if not row_has_minimum_data(row):
        errors.append(
            "Row needs at least one of project_title, artist_name, organization_name, or proof_external_url"
        )

    kind = clean_cell(row.get("contributor_kind")).lower()
    if kind and kind not in CONTRIBUTOR_KINDS:
        errors.append(f'Invalid contributor_kind "{kind}"')
    elif not kind:
        warnings.append("contributor_kind missing; should be person|organization|collective|unknown")

    year = clean_cell(row.get("year"))
    if year and not re.fullmatch(r"\d{4}", year):
        errors.append(f'year must be YYYY, got "{year}"')

    for field in ("artist_website", "proof_external_url", "source_record_url"):
        value = clean_cell(row.get(field))
        if value and not http_url(value):
            errors.append(f"{field} is not a valid http(s) URL")

    for field in ("image_urls",):
        value = clean_cell(row.get(field))
        if not value:
            continue
        if value.startswith("["):
            errors.append(f"{field} must be pipe-delimited URLs, not a JSON array literal")
            continue
        for part in value.split("|"):
            part = part.strip()
            if part and not http_url(part):
                errors.append(f"{field} contains invalid URL: {part}")

    for field, values in (
        ("source_granularity", SOURCE_GRANULARITY_VALUES),
        ("proof_confidence", CONFIDENCE_VALUES),
        ("classification_confidence", CONFIDENCE_VALUES),
        ("description_confidence", CONFIDENCE_VALUES),
        ("classification_source", CLASSIFICATION_SOURCE_VALUES),
        ("review_status", REVIEW_STATUS_VALUES),
        ("permission_status", PERMISSION_STATUS_VALUES),
    ):
        value = clean_cell(row.get(field))
        if value and value not in values:
            warnings.append(f'Unknown {field} "{value}"')

    for key, value in row.items():
        if value is None:
            errors.append(f"{key} is null")
        elif isinstance(value, str) and value.lower() in {"null", "none", "undefined"}:
            errors.append(f'{key} contains literal "{value}"')

    if kind == "organization" and not clean_cell(row.get("organization_name")):
        warnings.append("organization_name recommended for organization rows")
    if kind == "person" and not clean_cell(row.get("artist_name")):
        warnings.append("artist_name recommended for person rows")

    return errors, warnings


def validate_header(headers: Iterable[str]) -> list[str]:
    headers_list = list(headers)
    errors: list[str] = []
    expected_standard = list(STANDARD_COLUMNS)
    actual_standard = headers_list[: len(STANDARD_COLUMNS)]
    if actual_standard != expected_standard:
        errors.append(
            "Standard header order mismatch. "
            f"Expected first {len(STANDARD_COLUMNS)} columns to match artelier_scraper_csv_v1 exactly."
        )
    for column in EXPORT_COLUMNS:
        if column not in headers_list:
            errors.append(f"Missing export column {column}")
    return errors
