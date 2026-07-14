from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VERIFICATION_SCHEMA_VERSION = "archive-verification-v1"


@dataclass(frozen=True)
class WwwReferenceRecord:
    year: int
    title: str
    normalized_title: str
    description: str
    artist_url: str | None
    legacy_link: str | None
    uid: str | None
    theme_camp: str | None = None
    playa_address: str | None = None
    installation_type: str | None = None


@dataclass(frozen=True)
class ArchiveIndexRecord:
    year: str
    title: str
    normalized_title: str
    artist_display_text: str | None
    artist_location: str | None
    description: str | None
    website_url: str | None
    canonical_source_url: str
    uid: str | None
    image_urls: list[str] = field(default_factory=list)
    image_alt_text: str | None = None


@dataclass(frozen=True)
class ImageAsset:
    image_url: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    link_active: bool = False
    alt_text: str | None = None
    photographer_credit: str | None = None
    credit_text: str | None = None
    source_page_url: str | None = None
    source_type: str = "burning_man_official"
    attribution_confidence: str = "missing"
    review_required: bool = True
    validation_error: str | None = None


@dataclass
class VerificationResult:
    year: int
    project_title: str
    normalized_title: str
    verification_status: str
    archive_uid: str | None = None
    archive_url: str | None = None
    www_title: str | None = None
    www_uid: str | None = None
    legacy_link_status: str | None = None
    title_match_score: float | None = None
    artist_match_score: float | None = None
    description_match_score: float | None = None
    uid_match: bool | None = None
    archive_artist: str | None = None
    export_artist: str | None = None
    image_count: int = 0
    active_image_count: int = 0
    hero_image_url: str | None = None
    hero_image_active: bool | None = None
    public_credit_language: str | None = None
    warnings: list[str] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    source: str = "archive"

    def to_row(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "project_title": self.project_title,
            "verification_status": self.verification_status,
            "archive_uid": self.archive_uid or "",
            "archive_url": self.archive_url or "",
            "www_title": self.www_title or "",
            "www_uid": self.www_uid or "",
            "legacy_link_status": self.legacy_link_status or "",
            "title_match_score": "" if self.title_match_score is None else f"{self.title_match_score:.2f}",
            "artist_match_score": "" if self.artist_match_score is None else f"{self.artist_match_score:.2f}",
            "description_match_score": ""
            if self.description_match_score is None
            else f"{self.description_match_score:.2f}",
            "uid_match": "" if self.uid_match is None else str(self.uid_match),
            "archive_artist": self.archive_artist or "",
            "export_artist": self.export_artist or "",
            "image_count": self.image_count,
            "active_image_count": self.active_image_count,
            "hero_image_url": self.hero_image_url or "",
            "hero_image_active": "" if self.hero_image_active is None else str(self.hero_image_active),
            "public_credit_language": self.public_credit_language or "",
            "warnings": " | ".join(self.warnings),
            "source": self.source,
        }
