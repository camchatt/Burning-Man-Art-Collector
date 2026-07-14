from __future__ import annotations

import json
from pathlib import Path


REVIEW_FLAGS_ALLOWED = (
    "duplicate_candidate",
    "name_split_uncertain",
    "playa_name_uncertain",
    "contributor_kind_uncertain",
    "identity_needs_review",
    "hero_missing",
    "hero_needs_review",
    "missing_archive_cache",
    "honorarium_unknown",
)

# Exact extension column order after Artelier's 36 core columns.
# Note: do NOT duplicate core hero_image_url here (avoids hero_image_url.1).
BM_EXTENSION_HEADERS = (
    "bm_uid",
    "bm_year",
    "bm_event_name",
    "playa_address",
    "playa_latitude",
    "playa_longitude",
    "honorarium_status",
    "theme_camp",
    "installation_type",
    "source_artist_credit",
    "contributor_display_name",
    "additional_contributor_credits",
    "contributor_kind",
    "contributor_first_name",
    "contributor_last_name",
    "playa_name",
    "playa_name_confidence",
    "bm_hero_image_source_url",
    "hero_image_source_page",
    "hero_image_attribution",
    "hero_image_confidence",
    "review_flags",
    "source_provenance",
)

CONTRIBUTOR_KINDS = (
    "individual",
    "organization",
    "collective",
    "multiple",
    "studio",
    "theme_camp",
    "unknown",
)


def load_bm_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def default_bm_schema_path(project_root: Path) -> Path:
    return project_root / "config" / "burning_man_schema.json"
