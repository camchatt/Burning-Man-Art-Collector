from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from burning_man_scraper.exporters.artelier_scraper_csv_v1.contract import (
    ARTELIER_SCRAPER_CSV_V1,
    EXPORT_COLUMNS,
    SOURCE_NAME,
    SOURCE_NAMESPACE,
)
from burning_man_scraper.url_utils import encode_http_url


_LITERAL_NULL = re.compile(r"^(none|null|undefined|nan|-)$", re.IGNORECASE)


def clean_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or _LITERAL_NULL.match(text):
        return ""
    return text


def http_url(value: Any) -> str:
    text = clean_cell(value)
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.IGNORECASE):
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return encode_http_url(text)


def pipe_join(*parts: str) -> str:
    values: list[str] = []
    for part in parts:
        text = clean_cell(part)
        if not text:
            continue
        # Collapse accidental JSON array literals into pipe lists.
        if text.startswith("[") and text.endswith("]"):
            try:
                loaded = json.loads(text)
                if isinstance(loaded, list):
                    for item in loaded:
                        item_text = clean_cell(item)
                        if item_text and item_text not in values:
                            values.append(item_text)
                    continue
            except json.JSONDecodeError:
                pass
        for piece in re.split(r"[|;]+", text):
            piece = clean_cell(piece)
            if piece and piece not in values:
                values.append(piece)
    return "|".join(values)


def map_contributor_kind(raw: str) -> str:
    kind = clean_cell(raw).lower()
    if kind in {"person", "individual"}:
        return "person"
    if kind in {"organization", "studio", "theme_camp", "camp", "company", "nonprofit", "foundation"}:
        return "organization"
    if kind in {"collective", "crew"}:
        return "collective"
    return "unknown"


def map_permission_status(raw: str) -> str:
    value = clean_cell(raw)
    lowered = value.lower()
    aliases = {
        "pending_permission": "Needs permission",
        "needs permission": "Needs permission",
        "permission granted": "Permission granted",
        "permission not required": "Permission not required",
        "do not publish": "Do not publish",
        "unknown": "Unknown",
        "draft": "Needs permission",
    }
    return aliases.get(lowered, value if value in {
        "Needs permission",
        "Permission granted",
        "Permission not required",
        "Do not publish",
        "Unknown",
    } else "Needs permission")


def map_confidence(raw: str) -> str:
    value = clean_cell(raw).lower()
    if value in {"high", "medium", "low"}:
        return value
    return ""


def map_review_status(src: dict[str, str], *, kind: str) -> str:
    flags = clean_cell(src.get("review_flags"))
    flag_set = {part.strip() for part in flags.split("|") if part.strip()}
    if "duplicate_candidate" in flag_set:
        return "Duplicate"
    if not http_url(src.get("proof_external_url")) and not http_url(src.get("hero_image_source_page")):
        if not clean_cell(src.get("project_title")):
            return "Needs better source"
    if kind in {"collective", "unknown"} or "identity_needs_review" in flag_set or "contributor_kind_uncertain" in flag_set:
        return "Needs review"
    if flag_set:
        return "Needs review"
    return "Needs review"


def map_bm_upload_row(src: dict[str, str]) -> dict[str, str]:
    """Map one collector `artelier_bm_upload` row into artelier_scraper_csv_v1 (+ bm_*)."""
    kind = map_contributor_kind(src.get("contributor_kind") or "")
    display = clean_cell(src.get("contributor_display_name") or src.get("contributor_name"))
    raw_credit = clean_cell(src.get("source_artist_credit"))
    alias = clean_cell(src.get("playa_name"))
    additional = clean_cell(src.get("additional_contributor_credits"))

    artist_name = ""
    organization_name = ""
    if kind == "person":
        artist_name = display or raw_credit
    elif kind == "organization":
        organization_name = display or raw_credit
        # Keep artist_name blank unless a separate person credit exists (not invent).
        artist_name = ""
    elif kind == "collective":
        artist_name = display or raw_credit
        organization_name = display or raw_credit
    else:
        artist_name = display or raw_credit
        organization_name = ""

    year = clean_cell(src.get("project_year") or src.get("bm_year"))
    if year and not re.fullmatch(r"\d{4}", year):
        year = ""

    proof_url = http_url(src.get("proof_external_url"))
    source_page = http_url(src.get("hero_image_source_page"))
    source_record_url = proof_url or source_page
    website = http_url(src.get("contributor_website"))
    hero = http_url(src.get("hero_image_url") or src.get("bm_hero_image_source_url"))
    image_urls = pipe_join(hero)

    notes: list[str] = []
    flags = clean_cell(src.get("review_flags"))
    if flags:
        notes.append(f"collector_review_flags={flags}")
    if kind == "unknown":
        notes.append("Identity left unresolved for Artelier staging review.")
    if kind == "collective":
        notes.append("Collective credit preserved; do not auto-create a person contributor.")
    if additional:
        notes.append(f"Additional credits: {additional}")
    if alias and kind == "person":
        notes.append("artist_alias is a Burner / alternate public name from the collector.")

    location = clean_cell(src.get("project_location")) or clean_cell(src.get("playa_address"))
    if location and clean_cell(src.get("playa_address")) and location != clean_cell(src.get("playa_address")):
        # Prefer city/location for generic field; playa stays in bm_playa_address.
        pass

    bm_uid = clean_cell(src.get("bm_uid"))
    source_record_id = bm_uid or f"{year}-{clean_cell(src.get('project_slug'))}".strip("-")

    collaborators = pipe_join(additional)

    row = {column: "" for column in EXPORT_COLUMNS}
    row.update(
        {
            "contract_version": ARTELIER_SCRAPER_CSV_V1,
            "source_name": SOURCE_NAME,
            "source_namespace": SOURCE_NAMESPACE,
            "source_record_id": source_record_id,
            "source_record_url": source_record_url,
            "contributor_kind": kind,
            "artist_name": artist_name,
            "artist_alias": alias if kind in {"person", "collective", "unknown"} else "",
            "artist_website": website,
            "organization_name": organization_name,
            "project_title": clean_cell(src.get("project_title")),
            "proof_title": clean_cell(src.get("proof_title")) or clean_cell(src.get("project_title")),
            "proof_external_url": proof_url,
            "proof_excerpt": clean_cell(src.get("proof_description") or src.get("project_summary")),
            "source_granularity": "Individual project page" if proof_url or source_page else "Portfolio index page",
            "project_type": clean_cell(src.get("project_type") or src.get("installation_type")),
            "tags": pipe_join(src.get("project_tags") or ""),
            "materials": pipe_join(src.get("project_materials") or ""),
            "fabrication_methods": pipe_join(src.get("project_fabrication_methods") or ""),
            "context_tags": pipe_join(src.get("project_context_tags") or "", "playa"),
            "what_they_did": clean_cell(src.get("what_they_did")),
            "why_it_matters": clean_cell(src.get("why_it_mattered")),
            "contributor_role": clean_cell(src.get("role_title")),
            "collaboration_status": clean_cell(src.get("collaboration_status")),
            "collaborators": collaborators,
            "location": location,
            "year": year,
            "dimensions": "",
            "client_or_commissioner": clean_cell(src.get("client_name")),
            "institution": "",
            "image_urls": image_urls,
            "proof_confidence": "high" if proof_url else "low",
            "classification_confidence": map_confidence(src.get("project_classification_confidence")) or "medium",
            "description_confidence": "medium" if clean_cell(src.get("project_summary")) else "low",
            "classification_source": "scraper",
            "review_status": map_review_status(src, kind=kind),
            "permission_status": map_permission_status(src.get("permission_status") or ""),
            "import_notes": " | ".join(notes),
            "bm_year": clean_cell(src.get("bm_year") or year),
            "bm_uid": bm_uid,
            "bm_event_name": clean_cell(src.get("bm_event_name")),
            "bm_playa_address": clean_cell(src.get("playa_address")),
            "bm_latitude": clean_cell(src.get("playa_latitude")),
            "bm_longitude": clean_cell(src.get("playa_longitude")),
            "bm_honorarium_status": clean_cell(src.get("honorarium_status")),
            "bm_theme_camp": clean_cell(src.get("theme_camp")),
            "bm_installation_type": clean_cell(src.get("installation_type")),
            "bm_artist_text_raw": raw_credit or display,
            "bm_additional_credits": additional,
            "bm_review_flags": flags,
            "bm_source_provenance": clean_cell(src.get("source_provenance")),
            "bm_hero_image_source_url": http_url(src.get("bm_hero_image_source_url") or src.get("hero_image_url")),
            "bm_hero_image_source_page": source_page,
        }
    )
    return row
