from __future__ import annotations

import hashlib
import json
from pathlib import Path


ARTELIER_SCRAPER_CSV_V1 = "artelier_scraper_csv_v1"
SOURCE_NAME = "Burning Man Art Archive"
SOURCE_NAMESPACE = "burning_man"

STANDARD_COLUMNS: tuple[str, ...] = (
    "contract_version",
    "source_name",
    "source_namespace",
    "source_record_id",
    "source_record_url",
    "contributor_kind",
    "artist_name",
    "artist_alias",
    "artist_website",
    "organization_name",
    "project_title",
    "proof_title",
    "proof_external_url",
    "proof_excerpt",
    "source_granularity",
    "project_type",
    "tags",
    "materials",
    "fabrication_methods",
    "context_tags",
    "what_they_did",
    "why_it_matters",
    "contributor_role",
    "collaboration_status",
    "collaborators",
    "location",
    "year",
    "dimensions",
    "client_or_commissioner",
    "institution",
    "image_urls",
    "proof_confidence",
    "classification_confidence",
    "description_confidence",
    "classification_source",
    "review_status",
    "permission_status",
    "import_notes",
)

BM_EXTENSION_COLUMNS: tuple[str, ...] = (
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
    "bm_hero_image_source_url",
    "bm_hero_image_source_page",
)

EXPORT_COLUMNS: tuple[str, ...] = STANDARD_COLUMNS + BM_EXTENSION_COLUMNS

CONTRIBUTOR_KINDS = frozenset({"person", "organization", "collective", "unknown"})
SOURCE_GRANULARITY_VALUES = frozenset(
    {
        "Individual project page",
        "Portfolio index page",
        "Gallery caption",
        "Bio/CV reference",
        "Press article",
        "Image-only inference",
    }
)
CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
CLASSIFICATION_SOURCE_VALUES = frozenset({"manual", "scraper", "ai", "imported"})
REVIEW_STATUS_VALUES = frozenset(
    {
        "Needs review",
        "Approved",
        "Rejected",
        "Needs better source",
        "Needs permission",
        "Duplicate",
    }
)
PERMISSION_STATUS_VALUES = frozenset(
    {
        "Needs permission",
        "Permission granted",
        "Permission not required",
        "Do not publish",
        "Unknown",
    }
)


def contracts_dir(project_root: Path) -> Path:
    return project_root / "contracts" / "artelier-scraper-v1"


def load_contract_manifest(project_root: Path) -> dict:
    path = contracts_dir(project_root) / "contract.json"
    return json.loads(path.read_text(encoding="utf-8"))


def schema_sha256(project_root: Path) -> str:
    schema = contracts_dir(project_root) / "schema.json"
    return hashlib.sha256(schema.read_bytes()).hexdigest()


def verify_contract_checksum(project_root: Path) -> None:
    manifest = load_contract_manifest(project_root)
    expected = manifest.get("schema_sha256")
    actual = schema_sha256(project_root)
    if expected and expected != actual:
        raise ValueError(
            f"Contract schema checksum mismatch: expected {expected}, got {actual}. "
            "Re-copy Artelier contract artifacts into contracts/artelier-scraper-v1/."
        )
