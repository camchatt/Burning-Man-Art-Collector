"""Map internal records onto the authoritative 36-column Artelier import schema."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.sources.artist_website.images import preferred_hero_url


DEFAULTS_36 = {
    "project_visibility": "private",
    "contributor_visibility": "private",
    "contribution_visibility": "private",
    "proof_visibility": "private",
    "verification_status": "documented",
    "approval_status": "draft",
    "permission_status": "pending_permission",
}


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")


def artelier_headers(project_root: Path) -> list[str]:
    schema = load_import_schema(project_root / "config" / "artelier_import_schema.yaml")
    return list(schema.headers)


def join_list(values: Any, sep: str = "|") -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        return values.strip()
    if isinstance(values, (list, tuple)):
        return sep.join(str(item).strip() for item in values if str(item).strip())
    return str(values).strip()


def empty_artelier_row(headers: list[str]) -> dict[str, str]:
    row = {header: "" for header in headers}
    row.update(DEFAULTS_36)
    return row


def artist_internal_to_artelier36(row: dict[str, Any], headers: list[str]) -> dict[str, str]:
    """Map artist-site internal extraction rows to the 36-column Artelier core."""
    image_urls = row.get("image_urls") or []
    if not isinstance(image_urls, list):
        image_urls = [part for part in str(image_urls).split("|") if part.strip()]

    role_title = str(row.get("contributor_role") or "Artist").strip()
    collaboration_status = str(row.get("collaboration_status") or "").strip()
    if collaboration_status == "Solo project":
        contribution_title = "Primary artist and creator"
    elif role_title:
        contribution_title = f"{role_title} contribution"
    else:
        contribution_title = "Project contribution"

    proof_url = str(row.get("proof_external_url") or "").strip()
    what_they_did = str(row.get("what_they_did") or "").strip()
    title = str(row.get("project_title") or "").strip()
    artist = str(row.get("artist_name") or "").strip()

    out = empty_artelier_row(headers)
    out.update(
        {
            "project_title": title,
            "project_slug": slugify(title),
            "project_type": str(row.get("project_type") or "").strip(),
            "project_year": str(row.get("year") or "").strip(),
            "project_location": str(row.get("location") or "").strip(),
            "project_summary": what_they_did,
            "project_tags": join_list(row.get("tags")),
            "project_materials": join_list(row.get("materials")),
            "project_fabrication_methods": join_list(row.get("fabrication_methods")),
            "project_context_tags": join_list(row.get("context_tags")),
            "project_classification_confidence": str(
                row.get("classification_confidence") or ""
            )
            .strip()
            .lower(),
            "client_name": str(row.get("client_or_commissioner") or "").strip(),
            "hero_image_url": preferred_hero_url(image_urls, artist_name=artist),
            "contributor_name": artist,
            "contributor_slug": slugify(artist),
            "role_title": role_title,
            "contributor_email": "",
            "contributor_website": str(row.get("artist_website") or "").strip(),
            "collaboration_status": collaboration_status,
            "contribution_category": "",
            "contribution_title": contribution_title,
            "what_they_did": what_they_did,
            "why_it_mattered": str(row.get("why_it_matters") or "").strip(),
            "public_credit_language": "",
            "phase": "",
            "proof_title": str(row.get("proof_title") or title).strip(),
            "proof_type": str(row.get("source_granularity") or "").strip(),
            "proof_external_url": proof_url,
            "proof_description": str(row.get("proof_excerpt") or "").strip()[:700],
        }
    )
    return {header: out.get(header, "") for header in headers}


def export_blockers_for_row(row: dict[str, str], *, required: list[str] | None = None) -> list[str]:
    required = required or ["project_title", "project_slug", "proof_external_url"]
    blockers: list[str] = []
    for field in required:
        if not (row.get(field) or "").strip():
            blockers.append(f"missing_{field}")
    flags = [flag for flag in (row.get("review_flags") or "").split("|") if flag]
    attention = {
        "duplicate_candidate",
        "name_split_uncertain",
        "playa_name_uncertain",
        "contributor_kind_uncertain",
        "identity_needs_review",
        "hero_missing",
        "hero_needs_review",
        "missing_archive_cache",
        "sparse_evidence",
        "low_confidence",
        "missing_attribution",
        "incomplete_fields",
    }
    for flag in flags:
        if flag in attention:
            blockers.append(flag)
    return blockers
