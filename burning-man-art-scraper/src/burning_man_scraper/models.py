from __future__ import annotations

from pydantic import BaseModel, Field


SCHEMA_VERSION = "installation-preview-v1"


class InstallationRecord(BaseModel):
    record_id: str | None = None
    source_url: str | None = None
    canonical_source_url: str | None = None
    source_archive_url: str | None = None
    source_accessed_at: str | None = None
    scrape_run_id: str | None = None
    scraped_at: str | None = None
    parser_version: str | None = None
    schema_version: str = SCHEMA_VERSION
    title: str | None = None
    normalized_title: str | None = None
    year: str | None = None
    event_name: str | None = None
    event_theme: str | None = None
    installation_type: str | None = None
    honoraria_status: str | None = None
    funding_status: str | None = None
    artist_display_text: str | None = None
    artist_names: list[str] = Field(default_factory=list)
    artist_collective: str | None = None
    artist_location: str | None = None
    description: str | None = None
    materials: str | None = None
    dimensions: str | None = None
    location_on_playa: str | None = None
    website_url: str | None = None
    project_url: str | None = None
    external_links: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    primary_image_url: str | None = None
    image_alt_text: str | None = None
    photographer_credit: str | None = None
    image_credit_text: str | None = None
    extraction_confidence: float | None = None
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    parsing_errors: list[str] = Field(default_factory=list)
    needs_manual_review: bool | None = None
